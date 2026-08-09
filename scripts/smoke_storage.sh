#!/usr/bin/env bash
#
# Storage smoke test — proves the deployed API is really writing to the private
# S3-compatible bucket, not silently falling back to local disk.
#
# The tell is one field: the download link the API hands back. A presigned
# Supabase/R2 URL means the S3 backend is live; a `/api/storage/object/?key=…`
# path means S3_ENDPOINT_URL never took effect and uploads are landing on the
# host's ephemeral disk, where they vanish on the next restart. That failure is
# silent by design (the app degrades rather than erroring), which is exactly why
# it needs an explicit test.
#
# Creates a throwaway worker, inspects the link, then deletes the worker — which
# also exercises the bucket-cleanup path.
#
# Usage:
#   scripts/smoke_storage.sh [API_BASE_URL]
#   API=https://my-api.onrender.com scripts/smoke_storage.sh
#
set -uo pipefail

API="${1:-${API:-https://gate-intake-api.onrender.com}}"
API="${API%/}"
EMAIL="${CONTRACTOR_EMAIL:-contractor.one@vendor.com}"
PASSWORD="${CONTRACTOR_PASSWORD:-contractor_test_123}"
AADHAAR="${SMOKE_AADHAAR:-999999999999}"
TMP_PDF="$(mktemp -t smoke).pdf"
trap 'rm -f "$TMP_PDF"' EXIT

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m✖ %s\033[0m\n' "$*"; exit 1; }

jqp() { python3 -c "import sys,json;d=json.load(sys.stdin);$1"; }

say "0 · Waking $API (free tier sleeps after ~15 min idle)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 120 "$API/api/requirements/")
echo "   HTTP $code"
[ "$code" = "000" ] && fail "Could not reach $API — check the URL."

say "1 · Logging in as the contractor"
LOGIN=$(curl -s --max-time 60 -X POST "$API/api/auth/login/" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
TOKEN=$(echo "$LOGIN" | jqp 'print(d.get("token",""))' 2>/dev/null || true)
[ -z "$TOKEN" ] && fail "Login failed: $LOGIN"
echo "   token ${TOKEN:0:8}…"

# A previous run may have left the smoke worker behind; Aadhaar is UNIQUE.
say "2 · Clearing any leftover smoke worker"
curl -s --max-time 60 "$API/api/workers/" -H "Authorization: Token $TOKEN" \
  | jqp "print(next((str(w['id']) for w in d if w['aadhar_number']=='$AADHAAR'), ''))" \
  | while read -r old; do
      [ -n "$old" ] && curl -s -o /dev/null -X DELETE "$API/api/workers/$old/" \
        -H "Authorization: Token $TOKEN" && echo "   removed worker #$old"
    done

say "3 · Onboarding a throwaway worker with one attached document"
printf '%%PDF-1.4 storage smoke test' > "$TMP_PDF"
WORKER=$(curl -s --max-time 120 -X POST "$API/api/intake/onboard-worker/" \
  -H "Authorization: Token $TOKEN" \
  -F "name=Storage Smoke" -F "aadhar_number=$AADHAAR" -F "skill_type=Tester" \
  -F "aadhaar_file=@$TMP_PDF;type=application/pdf")
STORED=$(echo "$WORKER" | jqp 'print(",".join(d.get("documents_stored",[])) or "NONE")' 2>/dev/null || echo "PARSE_ERROR")
if [ "$STORED" = "PARSE_ERROR" ] || [ "$STORED" = "NONE" ]; then
  echo "   response: $WORKER"
  fail "Upload did not store the document — see the error above."
fi
WID=$(echo "$WORKER" | jqp 'print(d["worker"]["id"])')
echo "   worker #$WID · documents stored: $STORED"

say "4 · Where does the download link point?"
DOC_URL=$(curl -s --max-time 60 "$API/api/verification-status/" \
  -H "Authorization: Token $TOKEN" \
  | jqp "
row = next((r for r in d if r['aadhar_number']=='$AADHAAR'), None)
print(next((i['doc_url'] or '' for i in row['items'] if i['key']=='Aadhar'), '') if row else '')
")
echo "   $DOC_URL"

VERDICT_OK=0
case "$DOC_URL" in
  *supabase*|*r2.cloudflarestorage*|*X-Amz-Signature*)
    printf '\n\033[32m✅ VERDICT: presigned object storage is live\033[0m\n'
    VERDICT_OK=1 ;;
  /api/storage/object/*)
    printf '\n\033[31m❌ VERDICT: LOCAL FALLBACK — S3_ENDPOINT_URL is not in effect\033[0m\n'
    echo "   Uploads are on ephemeral disk and will vanish on restart." ;;
  "")
    printf '\n\033[31m❌ VERDICT: no document link returned\033[0m\n' ;;
  *)
    printf '\n\033[33m⚠ VERDICT: unrecognised link shape — inspect it above\033[0m\n' ;;
esac

if [ "$VERDICT_OK" = "1" ]; then
  say "5 · Fetching the presigned link"
  BODY=$(curl -s --max-time 60 "$DOC_URL" | head -c 40)
  echo "   $BODY"
  case "$BODY" in
    *"storage smoke test"*) printf '\033[32m   ✅ bytes round-tripped intact\033[0m\n' ;;
    *) printf '\033[31m   ✖ link did not serve the file (expired, or bucket ACL)\033[0m\n' ;;
  esac
fi

say "6 · Cleanup (also exercises bucket deletion)"
curl -s -o /dev/null -X DELETE "$API/api/workers/$WID/" -H "Authorization: Token $TOKEN"
echo "   deleted worker #$WID"
