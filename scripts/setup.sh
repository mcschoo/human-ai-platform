#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ ! -f .env ]; then
  cp .env.example .env
  key=$(sh "$ROOT/scripts/generate-api-key.sh")
  tmp=$(mktemp)
  sed "s/^VLLM_API_KEY=.*/VLLM_API_KEY=$key/" .env >"$tmp"
  mv "$tmp" .env
  chmod 600 .env
  echo "Created .env with a generated API key."
else
  echo "Using existing .env."
fi

docker compose config --quiet
docker compose up -d
docker compose ps
