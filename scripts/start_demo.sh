#!/bin/sh
set -eu
docker compose up --detach agent
echo "Demo: http://127.0.0.1:${APP_PORT:-8000}/api/v1/demo"
echo "API docs: http://127.0.0.1:${APP_PORT:-8000}/docs"
