#!/usr/bin/env sh
set -eu

if command -v openssl >/dev/null 2>&1; then
  openssl rand -hex 32
else
  python3 -c 'import secrets; print(secrets.token_hex(32))'
fi
