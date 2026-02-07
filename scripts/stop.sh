#!/bin/bash
set -e

echo "=========================================="
echo "  LegalSearch - Stop Services"
echo "=========================================="

cd "$(dirname "$0")/.."

echo "Stopping all services..."
docker compose down

echo "Services stopped."
