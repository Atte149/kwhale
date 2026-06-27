#!/usr/bin/env bash
# End-to-end smoke test for KWhale. Hits the public domain like a real client.
# Usage: bash scripts/smoke.sh   (override KWHALE_BASE/USER/PASS via env)
set -uo pipefail
BASE="${KWHALE_BASE:-http://localhost:19000}"
USER="${KWHALE_USER:-admin}"
PASS="${KWHALE_PASS:-}"
fail=0
ok(){ echo "  OK   $1"; }
bad(){ echo "  FAIL $1"; fail=1; }

echo "== health =="
for path in /healthz /rest/ping.view; do
  code=$(curl -sk -o /dev/null -w '%{http_code}' -m10 "$BASE$path")
  [ "$code" = 200 ] && ok "$path ($code)" || bad "$path ($code)"
done

echo "== auth =="
JWT=$(curl -sk -m10 -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("token",""))' 2>/dev/null)
if [ -n "$JWT" ]; then ok "login (token len ${#JWT})"; else bad "login"; echo "SMOKE: FAIL (no token)"; exit 1; fi

echo "== recommendations =="
recs=$(curl -sk -m25 "$BASE/api/recs?type=hybrid&limit=5" -H "Authorization: Bearer $JWT")
n=$(echo "$recs" | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("tracks",[])))' 2>/dev/null)
[ "${n:-0}" -ge 1 ] && ok "/api/recs hybrid ($n tracks)" || bad "/api/recs hybrid (n=${n:-0})"

echo "== stream/cover contract (load without auth header) =="
SU=$(echo "$recs" | python3 -c 'import sys,json;print(json.load(sys.stdin)["tracks"][0].get("streamUrl",""))' 2>/dev/null)
CU=$(echo "$recs" | python3 -c 'import sys,json;print(json.load(sys.stdin)["tracks"][0].get("coverUrl",""))' 2>/dev/null)
ccode=$(curl -sk -o /dev/null -w '%{http_code}' -m15 "$CU")
[ "$ccode" = 200 ] && ok "coverUrl ($ccode)" || bad "coverUrl ($ccode)"
scode=$(curl -sk -o /dev/null -w '%{http_code}' -m15 -r 0-2000 "$SU")
{ [ "$scode" = 200 ] || [ "$scode" = 206 ]; } && ok "streamUrl range ($scode)" || bad "streamUrl ($scode)"

echo
[ "$fail" -eq 0 ] && echo "SMOKE: PASS" || echo "SMOKE: FAIL"
exit "$fail"
