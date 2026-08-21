#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

git pull --ff-only origin main
docker compose -p stock_data_repo build contract-test migrate
docker compose -p stock_data_repo up -d --force-recreate migrate api worker mcp
docker compose -p stock_data_repo restart tunnel
docker compose -p stock_data_repo ps
