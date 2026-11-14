#!/usr/bin/env bash
# Purpose: start the local web-retrieval project; ends on compose completion.
# Services log to Docker's bounded json-file logs. No remote deployment.
set -euo pipefail
cd "$(dirname "$0")"
umask 077
if [[ ! -e .env ]]; then
  command -v openssl >/dev/null
  cp .env.example .env
  printf '\nSEARXNG_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
fi
docker compose config --quiet
docker compose up --build -d
echo 'Started. Use docker compose logs and /health to check upstream search availability.'
