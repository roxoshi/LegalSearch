#!/bin/bash

cd "$(dirname "$0")/.."

SERVICE="${1:-}"

if [ -n "$SERVICE" ]; then
    docker compose logs -f "$SERVICE"
else
    docker compose logs -f
fi
