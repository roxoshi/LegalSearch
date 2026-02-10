#!/bin/bash
set -e

# Orchestrate database sync from staging to a target host
# Usage: ./scripts/db-sync.sh [full|incremental] [target-host]

cd "$(dirname "$0")/.."

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

usage() {
    echo "Usage: $0 <mode> <target-host>"
    echo ""
    echo "Modes:"
    echo "  full         Full pg_dump/pg_restore (drops target tables)"
    echo "  incremental  Export only rows changed since last sync (JSONL)"
    echo ""
    echo "Examples:"
    echo "  $0 full myserver.example.com"
    echo "  $0 incremental myserver.example.com"
    exit 1
}

if [ $# -lt 2 ]; then
    usage
fi

MODE="$1"
TARGET_HOST="$2"

# Source staging environment
ENV_FILE="${ENV_FILE:-envs/.env.staging}"
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
else
    echo -e "${RED}Error: $ENV_FILE not found.${NC}"
    exit 1
fi

SSH_USER="${STAGING_SSH_USER:-$(whoami)}"
DUMP_DIR="${SYNC_DUMP_DIR:-.data/db-dumps}"
TIMESTAMP_FILE="${DUMP_DIR}/.last_sync_timestamp"
REMOTE_PROJECT_DIR="${REMOTE_PROJECT_DIR:-~/LegalSearch}"

mkdir -p "$DUMP_DIR"

case "$MODE" in
    full)
        echo -e "${GREEN}=== Full Database Sync ===${NC}"
        echo "Target: ${SSH_USER}@${TARGET_HOST}"

        # Step 1: Create dump locally
        echo -e "\n${GREEN}[1/3] Creating database dump...${NC}"
        ./scripts/db-dump.sh

        # Find the latest dump
        LATEST_DUMP=$(ls -1t "${DUMP_DIR}"/legalsearch_*.dump | head -1)
        DUMP_NAME=$(basename "$LATEST_DUMP")

        # Step 2: Transfer to target
        echo -e "\n${GREEN}[2/3] Transferring dump to ${TARGET_HOST}...${NC}"
        ssh "${SSH_USER}@${TARGET_HOST}" "mkdir -p ${REMOTE_PROJECT_DIR}/${DUMP_DIR}"
        scp "$LATEST_DUMP" "${SSH_USER}@${TARGET_HOST}:${REMOTE_PROJECT_DIR}/${DUMP_DIR}/${DUMP_NAME}"

        # Step 3: Restore on target (non-interactive)
        echo -e "\n${GREEN}[3/3] Restoring on target...${NC}"
        ssh "${SSH_USER}@${TARGET_HOST}" "cd ${REMOTE_PROJECT_DIR} && \
            docker compose exec -T db psql -U \${POSTGRES_USER:-user} -d \${POSTGRES_DB:-search_db} -c \
                'DROP TABLE IF EXISTS document_chunks CASCADE; DROP TABLE IF EXISTS documents CASCADE;' && \
            docker compose exec -T db pg_restore \
                -U \${POSTGRES_USER:-user} \
                -d \${POSTGRES_DB:-search_db} \
                --no-owner --no-privileges --clean --if-exists \
                < ${DUMP_DIR}/${DUMP_NAME} 2>/dev/null; \
            docker compose exec -T db psql -U \${POSTGRES_USER:-user} -d \${POSTGRES_DB:-search_db} < scripts/migrations.sql"

        # Update timestamp
        date -Iseconds > "$TIMESTAMP_FILE"
        echo -e "\n${GREEN}Full sync complete!${NC}"
        ;;

    incremental)
        echo -e "${GREEN}=== Incremental Database Sync ===${NC}"
        echo "Target: ${SSH_USER}@${TARGET_HOST}"

        # Determine since timestamp
        if [ -f "$TIMESTAMP_FILE" ]; then
            SINCE=$(cat "$TIMESTAMP_FILE")
            echo "Syncing changes since: $SINCE"
        else
            echo -e "${YELLOW}No previous sync timestamp found. Running full sync instead.${NC}"
            exec "$0" full "$TARGET_HOST"
        fi

        EXPORT_FILE="${DUMP_DIR}/incremental_$(date +%Y%m%d_%H%M%S).jsonl"

        # Step 1: Export changed rows
        echo -e "\n${GREEN}[1/3] Exporting changed rows...${NC}"
        python scripts/db-incremental-export.py --since "$SINCE" --output "$EXPORT_FILE"

        LINE_COUNT=$(wc -l < "$EXPORT_FILE")
        if [ "$LINE_COUNT" -eq 0 ]; then
            echo "No changes since last sync."
            rm -f "$EXPORT_FILE"
            exit 0
        fi
        echo "Exported $LINE_COUNT documents."

        # Step 2: Transfer to target
        echo -e "\n${GREEN}[2/3] Transferring export to ${TARGET_HOST}...${NC}"
        EXPORT_NAME=$(basename "$EXPORT_FILE")
        ssh "${SSH_USER}@${TARGET_HOST}" "mkdir -p ${REMOTE_PROJECT_DIR}/${DUMP_DIR}"
        scp "$EXPORT_FILE" "${SSH_USER}@${TARGET_HOST}:${REMOTE_PROJECT_DIR}/${DUMP_DIR}/${EXPORT_NAME}"

        # Step 3: Import on target
        echo -e "\n${GREEN}[3/3] Importing on target...${NC}"
        ssh "${SSH_USER}@${TARGET_HOST}" "cd ${REMOTE_PROJECT_DIR} && python scripts/db-incremental-import.py --input ${DUMP_DIR}/${EXPORT_NAME}"

        # Update timestamp
        date -Iseconds > "$TIMESTAMP_FILE"
        echo -e "\n${GREEN}Incremental sync complete ($LINE_COUNT documents)!${NC}"
        ;;

    *)
        echo -e "${RED}Error: Unknown mode '$MODE'${NC}"
        usage
        ;;
esac
