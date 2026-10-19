#!/usr/bin/env bash
# Smoke test for the deployed agent API (IMPLEMENTATION.md step 2.10). Run after every deploy:
#
#   scripts/smoke_test.sh https://ecommai-agent-xxxx.a.run.app [allowed origin, default http://localhost:3000]
#
# A full signed-in search needs a real Firebase ID token, so it isn't part of this test.
set -uo pipefail

url="${1:-${AGENT_URL:-}}"
url="${url%/}"
origin="${2:-http://localhost:3000}"
bad_origin="https://evil.example"
if [ -z "$url" ]; then
  echo "usage: $0 AGENT_URL [ALLOWED_ORIGIN]" >&2
  exit 2
fi

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }
ms() { awk '{ printf "%.0f ms", $1 * 1000 }'; }

# Before anything else, so the first request opens a new connection.
first=$(curl -sS -o /dev/null --max-time 30 -w '%{time_total}' "$url/health" | ms)
repeats=()
for _ in $(seq 11); do repeats+=(-o /dev/null "$url/health"); done
warm=$(curl -sS --max-time 60 -w '%{time_total}\n' "${repeats[@]}" \
  | tail -n +2 | sort -n | awk '{ t[NR] = $1 } END { print t[int((NR + 1) / 2)] }' | ms)

response=$(curl -sS --max-time 30 -w '\n%{http_code}' "$url/health")
code="${response##*$'\n'}"
body="${response%$'\n'*}"
if [ "$code" = 200 ] && [ "$(tr -d ' ' <<<"$body")" = '{"ok":true}' ]; then
  pass "GET /health -> 200 $body"
else
  fail "GET /health -> $code $body"
fi

code=$(curl -sS -o /dev/null --max-time 30 -w '%{http_code}' -X POST "$url/api/brief" \
  -H 'Content-Type: application/json' -d '{"text": "cotton kurta with pockets", "pincode": "110001"}')
if [ "$code" = 401 ]; then
  pass "POST /api/brief without a sign-in token -> 401"
else
  fail "POST /api/brief without a sign-in token -> $code (want 401)"
fi

preflight() {
  curl -sS -o /dev/null -D - --max-time 30 -X OPTIONS "$url/api/search" \
    -H "Origin: $1" -H 'Access-Control-Request-Method: POST' \
    -H 'Access-Control-Request-Headers: authorization,content-type' | tr -d '\r'
}

headers=$(preflight "$origin")
status=$(head -n 1 <<<"$headers" | awk '{ print $2 }')
if [ "$status" = 200 ] && grep -qi "^access-control-allow-origin: $origin\$" <<<"$headers"; then
  pass "CORS preflight from $origin allowed"
else
  fail "CORS preflight from $origin -> $status, allow-origin: $(grep -i '^access-control-allow-origin' <<<"$headers")"
fi

headers=$(preflight "$bad_origin")
status=$(head -n 1 <<<"$headers" | awk '{ print $2 }')
if ! grep -qi '^access-control-allow-origin' <<<"$headers"; then
  pass "CORS preflight from $bad_origin refused ($status, no allow-origin header)"
else
  fail "CORS preflight from $bad_origin allowed: $(grep -i '^access-control-allow-origin' <<<"$headers")"
fi

echo "INFO  /health latency: first request (new connection) $first, warm (median of 10, same connection) $warm"
echo "SKIP  signed-in search: needs a real Firebase ID token"
if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "All checks passed"
