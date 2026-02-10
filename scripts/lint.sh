#!/bin/bash
# Run linting and type checking before docker builds
# Usage: ./scripts/lint.sh [--fix]

set -e

cd "$(dirname "$0")/.."

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Running Linting & Type Checks${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

# Check if --fix flag is passed
FIX_MODE=""
if [[ "$1" == "--fix" ]]; then
    FIX_MODE="--fix"
    echo -e "${YELLOW}Running in FIX mode${NC}"
    echo ""
fi

# All project packages and directories to check
PACKAGES="backend pipelines etl scripts"

# Step 1: Ruff linting
echo -e "${GREEN}[1/3] Running Ruff linter on all packages...${NC}"
if [[ -n "$FIX_MODE" ]]; then
    uv run ruff check $PACKAGES --fix || true
    uv run ruff format $PACKAGES
else
    uv run ruff check $PACKAGES || {
        echo -e "${RED}✗ Ruff found errors${NC}"
        exit 1
    }
    uv run ruff format $PACKAGES --check || {
        echo -e "${RED}✗ Ruff format check failed${NC}"
        echo -e "${YELLOW}Run: ./scripts/lint.sh --fix${NC}"
        exit 1
    }
fi
echo -e "${GREEN}✓ Ruff checks passed${NC}"
echo ""

# Step 2: MyPy type checking
echo -e "${GREEN}[2/3] Running MyPy type checker...${NC}"
uv run mypy $PACKAGES --no-error-summary || {
    echo -e "${RED}✗ MyPy found type errors${NC}"
    echo -e "${YELLOW}Tip: Fix type errors before building Docker image${NC}"
    exit 1
}
echo -e "${GREEN}✓ MyPy checks passed${NC}"
echo ""

# Step 3: Run tests
echo -e "${GREEN}[3/3] Running tests...${NC}"
uv run pytest backend/tests pipelines/tests etl/tests -v --tb=short || {
    echo -e "${RED}✗ Tests failed${NC}"
    exit 1
}
echo -e "${GREEN}✓ All tests passed${NC}"
echo ""

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  All checks passed! Safe to build.${NC}"
echo -e "${GREEN}========================================${NC}"
