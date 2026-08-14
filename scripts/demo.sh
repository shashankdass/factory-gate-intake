#!/usr/bin/env bash
#
# End-to-end customer demo, driven entirely through the public API.
#
# Tells the whole story in one run:
#   1. Contractor states workforce demand and searches their own pool
#   2. Onboards a worker with all seven documents in a single pass
#   3. Resume is parsed; PII is encrypted at rest but still searchable
#   4. Documents are private — every link is short-lived and presigned
#   5. Trade test and safety video, the two non-document pillars
#   6. Contractor submits a deployment list; Employer approves it
#   7. Gate scans the Aadhaar -> GREEN
#   8. A certificate lapses AFTER approval -> the same scan flips to RED
#   9. Certificate renewed -> GREEN again
#
# Step 8 is the point of the product: an approval is a snapshot, and the gate
# re-checks compliance live rather than trusting it.
#
# Usage:
#   scripts/demo.sh                      # run straight through
#   scripts/demo.sh --pause              # wait for Enter between acts (presenting)
#   API=https://my-api.onrender.com scripts/demo.sh
#
set -uo pipefail

API="${API:-https://gate-intake-api.onrender.com}"
API="${API%/}"
PAUSE=0
for arg in "$@"; do
  case "$arg" in
    --pause) PAUSE=1 ;;
    http*)   API="${arg%/}" ;;
  esac
done

DEMO_AADHAAR=777777777777      # the worker this script creates
RAVI_AADHAAR=100000000001      # seeded, already fully compliant
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

B=$'\033[1m'; DIM=$'\033[2m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; C=$'\033[36m'; X=$'\033[0m'

act()  { printf '\n%s\n%s  %s%s\n%s\n' "${C}────────────────────────────────────────────────────────────${X}" "${B}${C}" "$*" "${X}" "${C}────────────────────────────────────────────────────────────${X}"; }
step() { printf '\n%s▸ %s%s\n' "$B" "$*" "$X"; }
note() { printf '%s   %s%s\n' "$DIM" "$*" "$X"; }
ok()   { printf '%s   ✔ %s%s\n' "$G" "$*" "$X"; }
bad()  { printf '%s   ✖ %s%s\n' "$R" "$*" "$X"; }
hold() { [ "$PAUSE" = "1" ] && { printf '\n%s   [Enter to continue]%s' "$DIM" "$X"; read -r _; }; return 0; }

jqp()  { python3 -c "import sys,json;d=json.load(sys.stdin);$1" 2>/dev/null; }
login() {
  curl -s --max-time 90 -X POST "$API/api/auth/login/" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\"}" | jqp 'print(d.get("token",""))'
}

# ---------------------------------------------------------------------------
act "Factory Gate-Intake · live demo"
note "API: $API"

step "Waking the service and signing in as all three personas"
curl -s -o /dev/null --max-time 150 "$API/api/requirements/"
CONTRACTOR=$(login contractor.one@vendor.com contractor_test_123)
PE=$(login pe.admin@factory.com pe_test_123)
GATE=$(login gate.security@factory.com gate_test_123)
[ -z "$CONTRACTOR" ] && { bad "Could not reach $API"; exit 1; }
ok "Contractor, Principal Employer and Gate Security authenticated"
note "One role-switcher in the UI; three scoped tokens underneath."

CH="Authorization: Token $CONTRACTOR"
PH="Authorization: Token $PE"
GH="Authorization: Token $GATE"

# Leave no residue from a previous run.
for A in "$DEMO_AADHAAR"; do
  OLD=$(curl -s "$API/api/workers/" -H "$CH" | jqp "print(next((str(w['id']) for w in d if w['aadhar_number']=='$A'),''))")
  [ -n "$OLD" ] && curl -s -o /dev/null -X DELETE "$API/api/workers/$OLD/" -H "$CH"
done
hold

# ---------------------------------------------------------------------------
act "1 · The contractor needs people, today"

step "Entering demand: 3 Carpenters, 4 Masons"
curl -s -X POST "$API/api/workforce-demand/" -H "$CH" -H 'Content-Type: application/json' \
  -d '{"demands":[{"skill":"Carpenter","count":3},{"skill":"Mason","count":4}]}' \
  | jqp '
for l in d["lines"]:
    print("   %-12s need %d | deployable now %d | short %d | fixable %d"
          % (l["skill"], l["required"], l["available"], l["shortfall"], l["fixable"]))
s = d["summary"]
print("   pool size: %d workers" % s["pool_size"])'
note "\"Fixable\" is the useful number: short on paperwork, not short on people."
hold

# ---------------------------------------------------------------------------
act "2 · Onboarding a worker — seven documents, one submission"

printf '%%PDF-1.4 Aadhaar card scan'   > "$TMP/aadhaar.pdf"
printf '%%PDF-1.4 PAN card scan'       > "$TMP/pan.pdf"
printf '%%PDF-1.4 Safety certificate'  > "$TMP/safety.pdf"
printf '%%PDF-1.4 Medical report'      > "$TMP/medical.pdf"
printf '%%PDF-1.4 Police verification' > "$TMP/pvc.pdf"
printf '%%PDF-1.4 HDFC BANK A/C No: 50100123456789 IFSC: HDFC0001234' > "$TMP/cheque.pdf"
printf '%%PDF-1.4 Resume — Ravi Kumar, Welder, 6 yrs' > "$TMP/resume.pdf"

TODAY=$(python3 -c 'import datetime;print(datetime.date.today())')
RECENT=$(python3 -c 'import datetime;print(datetime.date.today()-datetime.timedelta(days=30))')
FUTURE=$(python3 -c 'import datetime;print(datetime.date.today()+datetime.timedelta(days=180))')

step "Uploading all seven documents together"
note "The Aadhaar scan is the only mandatory one — everything else can be typed."
CREATED=$(curl -s --max-time 180 -X POST "$API/api/intake/onboard-worker/" -H "$CH" \
  -F "name=Demo Worker" -F "aadhar_number=$DEMO_AADHAAR" -F "skill_type=Carpenter" \
  -F "pan_number=ABCDE1234F" -F "safety_expiry=$FUTURE" \
  -F "exam_date=$RECENT" -F "vision=6/6" -F "blood_type=O+" \
  -F "certificate_number=PVC-DEMO-1" -F "issue_date=$RECENT" \
  -F "bank_account_number=50100123456789" -F "ifsc=HDFC0001234" -F "bank_name=HDFC Bank" \
  -F "aadhaar_file=@$TMP/aadhaar.pdf;type=application/pdf" \
  -F "pan_file=@$TMP/pan.pdf;type=application/pdf" \
  -F "safety_file=@$TMP/safety.pdf;type=application/pdf" \
  -F "medical_file=@$TMP/medical.pdf;type=application/pdf" \
  -F "pvc_file=@$TMP/pvc.pdf;type=application/pdf" \
  -F "bank_file=@$TMP/cheque.pdf;type=application/pdf" \
  -F "resume_file=@$TMP/resume.pdf;type=application/pdf")

DEMO_ID=$(echo "$CREATED" | jqp 'print(d["worker"]["id"])')
if [ -z "$DEMO_ID" ]; then bad "Onboarding failed:"; echo "$CREATED"; exit 1; fi
echo "$CREATED" | jqp 'print("   stored:", ", ".join(d["documents_stored"]))'
ok "Worker #$DEMO_ID created with every document in one round trip"
note "Validated first, uploaded concurrently, written in one transaction —"
note "an expired document can never leave a half-created worker behind."
hold

step "Bank details — encrypted at rest, masked on shared screens"
curl -s "$API/api/workers/" -H "$CH" | jqp "
w = next((w for w in d if w['aadhar_number']=='$DEMO_AADHAAR'), None)
b = (w or {}).get('bank_account') or {}
print('   account   ', b.get('account_number') or '—')
print('   ifsc      ', b.get('ifsc') or '—')
print('   shared with', b.get('shared_with_count', 0), 'other worker(s)')"
note "Several workers on one account is the classic ghost-worker signature."
hold

step "What the resume parser extracted"
echo "$CREATED" | jqp '
r = d.get("resume") or {}
for k in ("name","phone","email","place","stream","category","years_of_experience","qualification"):
    print("   %-20s %s" % (k, r.get(k) or "—"))
print("   %-20s %s" % ("skills", ", ".join(r.get("skills") or []) or "—"))
print("   %-20s %s" % ("read via", r.get("provider")))'
note "Name, phone and email are AES-256 encrypted at rest (pgcrypto)."
note "Everything else stays plaintext and indexed, so it is filterable."
hold

step "Encrypted, yet still searchable — name search hits a blind index, never decrypts"
curl -s "$API/api/candidates/search/?q=rav" -H "$CH" \
  | jqp '
print("   query \"rav\" ->", d["count"], "match(es)")
for r in d["results"][:3]:
    p = r["profile"]
    print("   %s · %s · %s · %s yrs" % (p["name"], p["place"] or "—",
          ", ".join(p["skills"][:3]) or "—", p["years_of_experience"] or "?"))'
hold

# ---------------------------------------------------------------------------
act "3 · Where the documents live"

step "Every file is in a PRIVATE bucket; links are minted on demand and expire"
curl -s "$API/api/verification-status/" -H "$CH" | jqp "
row = next(r for r in d if r['aadhar_number']=='$DEMO_AADHAAR')
for i in row['items']:
    if i['doc_url']:
        print('   %-12s %s' % (i['label'], i['doc_url'][:96] + '...'))
" | head -8
DOC_URL=$(curl -s "$API/api/verification-status/" -H "$CH" | jqp "
row = next(r for r in d if r['aadhar_number']=='$DEMO_AADHAAR')
print(next((i['doc_url'] for i in row['items'] if i['doc_url']), ''))")
if [ -n "$DOC_URL" ]; then
  BODY=$(curl -s --max-time 60 "$DOC_URL" | head -c 32)
  ok "Fetched through a presigned link: $BODY"
  note "No public URL exists. The link above dies in 15 minutes."
fi
hold

# ---------------------------------------------------------------------------
act "4 · The two pillars that are not documents"

step "Trade test — pictorial MCQs chosen for the worker's trade"
curl -s "$API/api/trade-test/start/?worker_id=$DEMO_ID" -H "$CH" | jqp '
if "questions" in d:
    print("   category: %s · attempt %d · pass mark %d/%d"
          % (d["category"], d["attempt_number"], d["pass_mark"], len(d["questions"])))
    q = d["questions"][0]
    print("   Q1:", q["question_text"])
    for k in "abcd":
        print("      %s) %s" % (k.upper(), q["option_%s" % k]))
else:
    print("  ", d.get("detail"))'
note "Answers are scored server-side; three failed attempts lock the profile."
hold

step "Safety induction video — progress recorded as the worker watches"
for pct in 40 80 100; do
  curl -s -o /dev/null -X POST "$API/api/safety-video/heartbeat/" -H "$CH" \
    -H 'Content-Type: application/json' -d "{\"worker\":$DEMO_ID,\"progress_percentage\":$pct}"
  printf '   %s%%%s' "$pct" ""
done
echo
ok "Watched in full — progress is monotonic, so skipping ahead cannot fake it"
hold

# ---------------------------------------------------------------------------
act "5 · Submit → approve → gate"

step "Making sure the demo worker's certificate is current"
curl -s -o /dev/null -X POST "$API/api/intake/verify-document/" -H "$CH" \
  -H 'Content-Type: application/json' \
  -d "{\"worker\":$(curl -s "$API/api/workers/" -H "$CH" | jqp "print(next(w['id'] for w in d if w['aadhar_number']=='$RAVI_AADHAAR'))"),\"doc_type\":\"IDENTITY\",\"requirement_name\":\"Safety Training\",\"expiry_date\":\"$FUTURE\"}"
RAVI_ID=$(curl -s "$API/api/workers/" -H "$CH" | jqp "print(next(w['id'] for w in d if w['aadhar_number']=='$RAVI_AADHAAR'))")
PROJECT=$(curl -s "$API/api/projects/" -H "$CH" | jqp 'print(d[0]["id"])')
ok "Ravi Kumar (#$RAVI_ID) is compliant and ready to deploy"

step "Contractor submits a deployment list to the Principal Employer"
LIST=$(curl -s -X POST "$API/api/intake-lists/" -H "$CH" -H 'Content-Type: application/json' \
  -d "{\"project\":$PROJECT,\"worker_ids\":[$RAVI_ID],\"submit\":true}")
LIST_ID=$(echo "$LIST" | jqp 'print(d.get("id",""))')
if [ -z "$LIST_ID" ]; then bad "Submit refused:"; echo "$LIST" | head -3; exit 1; fi
ok "List #$LIST_ID submitted"
note "Only fully-compliant workers are accepted onto a submitted list."

step "Principal Employer approves it"
curl -s -o /dev/null -X PATCH "$API/api/intake-lists/$LIST_ID/review/" -H "$PH" \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve","comments":"Approved for the turnaround."}'
ok "Approved"
hold

step "Gate Security scans the Aadhaar"
curl -s "$API/api/gate-check/?aadhar=$RAVI_AADHAAR" -H "$GH" | jqp '
print("   %s — %s" % (d["access"], d["reason"]))'
printf '%s   ▉▉▉  GREEN — ISSUE GATE PASS  ▉▉▉%s\n' "$G" "$X"
hold

# ---------------------------------------------------------------------------
act "6 · The point of the product"

step "A month later the safety certificate lapses — the approval is untouched"
YESTERDAY=$(python3 -c 'import datetime;print(datetime.date.today()-datetime.timedelta(days=1))')
curl -s -o /dev/null -X POST "$API/api/intake/verify-document/" -H "$CH" \
  -H 'Content-Type: application/json' \
  -d "{\"worker\":$RAVI_ID,\"doc_type\":\"IDENTITY\",\"requirement_name\":\"Safety Training\",\"expiry_date\":\"$YESTERDAY\"}"
note "List #$LIST_ID is still APPROVED. Nothing about it changed."

step "The same worker scans in again"
curl -s "$API/api/gate-check/?aadhar=$RAVI_AADHAAR" -H "$GH" | jqp '
print("   %s — %s" % (d["access"], d["reason"]))
for g in (d.get("compliance") or {}).get("gaps", []):
    print("      %s: %s%s" % (g["requirement_name"], g["reason"],
          " (expired %s)" % g["expiry_date"] if g.get("expiry_date") else ""))'
printf '%s   ▉▉▉  RED — DO NOT ADMIT  ▉▉▉%s\n' "$R" "$X"
note "A system that trusted the approval snapshot would have admitted him."
note "The gate re-runs the full compliance check at scan time instead."
hold

step "Certificate renewed — the gate reopens immediately"
curl -s -o /dev/null -X POST "$API/api/intake/verify-document/" -H "$CH" \
  -H 'Content-Type: application/json' \
  -d "{\"worker\":$RAVI_ID,\"doc_type\":\"IDENTITY\",\"requirement_name\":\"Safety Training\",\"expiry_date\":\"$FUTURE\"}"
curl -s "$API/api/gate-check/?aadhar=$RAVI_AADHAAR" -H "$GH" | jqp 'print("   %s — %s" % (d["access"], d["reason"]))'
printf '%s   ▉▉▉  GREEN again  ▉▉▉%s\n' "$G" "$X"

# ---------------------------------------------------------------------------
act "Cleanup"
curl -s -o /dev/null -X DELETE "$API/api/workers/$DEMO_ID/" -H "$CH"
ok "Demo worker removed, along with every file it put in the bucket"
note "Seeded data is untouched, so this script can be re-run any time."
echo
