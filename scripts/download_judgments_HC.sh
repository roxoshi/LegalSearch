#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Download High Court GST judgments from S3
#
# Usage:
#   ./scripts/download_judgments_HC.sh                   # all GST years (2017-2025)
#   ./scripts/download_judgments_HC.sh --years 2020 2021 # specific years
#   ./scripts/download_judgments_HC.sh --dry-run          # preview without downloading
#   ./scripts/download_judgments_HC.sh --list-years       # list available years in S3
# ──────────────────────────────────────────────────────────────

S3_BUCKET="s3://indian-high-court-judgments/data/tar"
JUDGMENTS_DIR=".data/GST_judgments"
DEFAULT_YEARS=(2017 2018 2019 2020 2021 2022 2023 2024 2025)
DRY_RUN=false
LIST_YEARS=false
YEARS=()

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Download High Court judgment tars from S3.

Options:
  --years YEAR [YEAR ...]   Years to download (default: 2017-2025)
  --output-dir DIR          Output directory (default: $JUDGMENTS_DIR)
  --dry-run                 Show what would be downloaded without downloading
  --list-years              List all available years in the S3 bucket and exit
  -h, --help                Show this help message

Examples:
  $(basename "$0")                         # Download all GST years
  $(basename "$0") --years 2020 2021       # Download specific years
  $(basename "$0") --dry-run --years 2023  # Preview 2023 download
EOF
    exit 0
}

# ── Parse arguments ──────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --years)
            shift
            while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                YEARS+=("$1")
                shift
            done
            ;;
        --output-dir)
            JUDGMENTS_DIR="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --list-years)
            LIST_YEARS=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Error: Unknown option '$1'" >&2
            usage
            ;;
    esac
done

# Use defaults if no years specified
if [[ ${#YEARS[@]} -eq 0 ]]; then
    YEARS=("${DEFAULT_YEARS[@]}")
fi

# ── Preflight checks ────────────────────────────────────────
if ! command -v aws &>/dev/null; then
    echo "Error: AWS CLI not found. Install with: pip install awscli" >&2
    exit 1
fi

# ── List years mode ──────────────────────────────────────────
if $LIST_YEARS; then
    echo "Available years in S3:"
    aws s3 ls "$S3_BUCKET/" --no-sign-request \
        | sed -n 's/.*PRE year=\([0-9]*\)\//  \1/p' \
        | sort -n
    exit 0
fi

# ── Discovery: count courts/benches per year ─────────────────
echo "Discovering files for ${#YEARS[@]} year(s)..."
echo ""

declare -A YEAR_FILES YEAR_SIZES
TOTAL_FILES=0
TOTAL_SIZE=0

for YEAR in "${YEARS[@]}"; do
    # Summarize what S3 has for this year (recursive listing)
    LISTING=$(aws s3 ls "$S3_BUCKET/year=$YEAR/" --recursive --no-sign-request --summarize 2>&1)

    FILE_COUNT=$(echo "$LISTING" | grep "Total Objects:" | awk '{print $3}')
    SIZE_BYTES=$(echo "$LISTING" | grep "Total Size:" | awk '{print $3}')

    FILE_COUNT=${FILE_COUNT:-0}
    SIZE_BYTES=${SIZE_BYTES:-0}

    YEAR_FILES[$YEAR]=$FILE_COUNT
    YEAR_SIZES[$YEAR]=$SIZE_BYTES
    TOTAL_FILES=$((TOTAL_FILES + FILE_COUNT))
    TOTAL_SIZE=$((TOTAL_SIZE + SIZE_BYTES))

    SIZE_HR=$(numfmt --to=iec-i --suffix=B "$SIZE_BYTES" 2>/dev/null || echo "${SIZE_BYTES} bytes")
    printf "  %s: %4d files (%s)\n" "$YEAR" "$FILE_COUNT" "$SIZE_HR"
done

TOTAL_SIZE_HR=$(numfmt --to=iec-i --suffix=B "$TOTAL_SIZE" 2>/dev/null || echo "${TOTAL_SIZE} bytes")
echo ""
echo "Total: $TOTAL_FILES files ($TOTAL_SIZE_HR)"
echo "Destination: $JUDGMENTS_DIR"
echo ""

if $DRY_RUN; then
    echo "[DRY RUN] No files downloaded."
    exit 0
fi

# ── Download ─────────────────────────────────────────────────
mkdir -p "$JUDGMENTS_DIR"

SUCCEEDED=0
FAILED=0
FAILED_YEARS=()
DOWNLOADED_SIZE=0

for i in "${!YEARS[@]}"; do
    YEAR="${YEARS[$i]}"
    YEAR_DIR="$JUDGMENTS_DIR/HC-GST-$YEAR"
    YEAR_NUM=$((i + 1))
    FILE_COUNT=${YEAR_FILES[$YEAR]}
    SIZE_HR=$(numfmt --to=iec-i --suffix=B "${YEAR_SIZES[$YEAR]}" 2>/dev/null || echo "${YEAR_SIZES[$YEAR]} bytes")

    echo "[$YEAR_NUM/${#YEARS[@]}] Syncing $YEAR ($FILE_COUNT files, $SIZE_HR)..."

    mkdir -p "$YEAR_DIR"

    if aws s3 sync "$S3_BUCKET/year=$YEAR/" "$YEAR_DIR" \
        --no-sign-request \
        --exclude "*" \
        --include "*.tar" \
        --include "*.json" \
        2>&1 | while IFS= read -r line; do
            # Show individual file progress from aws s3 sync
            if [[ "$line" == *"download:"* ]]; then
                FILE=$(echo "$line" | sed 's/.*download: .* to //')
                printf "  -> %s\n" "$(basename "$FILE")"
            fi
        done
    then
        SUCCEEDED=$((SUCCEEDED + 1))
        DOWNLOADED_SIZE=$((DOWNLOADED_SIZE + YEAR_SIZES[$YEAR]))
        DL_HR=$(numfmt --to=iec-i --suffix=B "$DOWNLOADED_SIZE" 2>/dev/null || echo "$DOWNLOADED_SIZE bytes")
        echo "  Done ($DL_HR downloaded so far)"
    else
        FAILED=$((FAILED + 1))
        FAILED_YEARS+=("$YEAR")
        echo "  FAILED for $YEAR"
    fi
    echo ""
done

# ── Summary ──────────────────────────────────────────────────
echo "========================================"
echo "Download complete"
echo "  Succeeded: $SUCCEEDED/${#YEARS[@]} years"
if [[ $FAILED -gt 0 ]]; then
    echo "  Failed:    $FAILED (${FAILED_YEARS[*]})"
fi
FINAL_HR=$(numfmt --to=iec-i --suffix=B "$DOWNLOADED_SIZE" 2>/dev/null || echo "$DOWNLOADED_SIZE bytes")
echo "  Total downloaded: $FINAL_HR"
echo "  Output: $JUDGMENTS_DIR"
echo "========================================"

exit $FAILED
