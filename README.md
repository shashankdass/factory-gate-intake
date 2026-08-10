# Factory Gate-Intake Optimization

A digital factory **gate-intake** platform that compresses worker onboarding
verification from **~5 days to under 24 hours**. React SPA + Django REST Framework
+ PostgreSQL, structured as a single monorepo for a one-push free deploy to Render.

```
factory-gate-intake/
├── backend/        # Django REST Framework API + Postgres models + data seed
│   ├── intake/
│   │   ├── crypto.py    # pgcrypto PII encryption + keyed blind indexes
│   │   ├── storage.py   # private S3-compatible bucket + presigned URLs
│   │   ├── resume.py    # pluggable resume → structured JSON parser
│   │   └── ocr.py       # document OCR with a never-fail contract
│   └── tests/      # pytest suite (no DB server, no keys, no network)
├── frontend/       # React (Vite) SPA with role-switching header + vitest tests
├── render.yaml     # Render Blueprint (API + static web + managed Postgres)
├── DEPLOY.md       # Step-by-step free Render deployment guide
└── .gitignore
```

## Personas & workflow

Operational ownership sits with the **Contractor**. They own their worker pool
end to end; the Principal Employer only reviews what is submitted; Gate Security
only scans.

| Persona | Does | Dashboard |
|---|---|---|
| **Contractor** | Owns their worker pool. Enters workforce demand ("3 Carpenters, 4 Masons") and searches their own pool, onboards workers with all six documents in one pass via the Unified Intake overlay, administers pictorial trade tests, tracks safety-video progress, re-verifies individual documents, and submits deployment lists | `/contractor` |
| **Principal Employer (PE)** | Reviews submitted lists and approves / requests changes / rejects with comments. Nothing else — project and requirement configuration were removed from this view | `/employer` |
| **Gate Security** | Fast Aadhaar lookup → **real-time** GREEN/RED entry decision | `/gate` |

### Dummy test credentials (seeded automatically)

| Role | Email | Password |
|---|---|---|
| Principal Employer | `pe.admin@factory.com` | `pe_test_123` |
| Contractor | `contractor.one@vendor.com` | `contractor_test_123` |
| Gate Security | `gate.security@factory.com` | `gate_test_123` |

The header **role-switcher** logs into each persona behind the scenes and swaps
the active token — no manual login needed for testing.

## Core logic: compliance evaluation

`Worker.compliance_against_project(project)` (in `backend/intake/models.py`) is the
single source of truth. For each **mandatory** project requirement it picks the
worker's best document and classifies any gap precisely:

- `MISSING` — no document for this requirement
- `PENDING` — uploaded but not yet verified
- `REJECTED` — verifier rejected it (with reason)
- `EXPIRED` — verified but past `expiry_date` (only for `is_expirable` requirements)

Only a **Verified, non-expired** document counts. On top of the project's
documents it merges the worker-global intake pillars — Medical, Police
verification, Trade Test, Safety Training Video and Resume — so a failure in any
of them blocks deployment too. The resume pillar is reported as an *advisory*
unless `REQUIRE_RESUME_FOR_COMPLIANCE=True`.

`/api/projects/<id>/eligible-workers/` uses this to split workers into
`ready_to_deploy` vs `needs_fixes` with the exact gap list per worker.

### Real-time gate compliance

An approved intake list is a **snapshot** taken when the PE signed off. Documents
keep expiring afterwards. `Worker.gate_decision()` therefore re-runs the full
compliance evaluation **at scan time**:

| Outcome | `reason_code` |
|---|---|
| Approved and everything currently valid | `COMPLIANT` → GREEN |
| Approved, but a document lapsed since | `DOCUMENT_EXPIRED` → **RED** |
| Approved, but a pillar regressed | `COMPLIANCE_REGRESSED` → RED |
| Not on any approved list | `NOT_APPROVED` → RED |
| Unknown Aadhaar | `UNKNOWN_WORKER` → RED |

The denial payload carries the full gap list, so the guard can tell the worker
exactly what to go and fix.

## Resume scanning, PII encryption and secure storage

**Parsing.** A PDF or photographed CV is parsed into structured JSON by a
pluggable provider (`RESUME_PARSER_PROVIDER`): `claude` (Anthropic vision, schema
constrained), `gemini`, `ocr` (text + heuristics, no key required — the default),
or `mock`. Every provider degrades to empty fields plus a note rather than
raising, so a bad scan never blocks onboarding.

**PII is encrypted at rest.** Candidate `name` / `phone` / `email` are stored
**only** as AES-256 ciphertext in `BYTEA` columns, produced by pgcrypto's
`pgp_sym_encrypt` via the `app_encrypt()` helper (migration `0007`). The
passphrase is bound per statement from `PII_ENCRYPTION_KEY` and never lives in
the database.

**Encrypted columns are still searchable.** `pgp_sym_encrypt` is randomised, so
the same email yields different ciphertext every time and a `UNIQUE` index on it
is useless. A keyed **blind index** — a deterministic HMAC-SHA256 under a
*separate* pepper (`PII_BLIND_INDEX_KEY`), computed in the application process so
the pepper never reaches the database — restores equality lookups and duplicate
detection. Name search additionally stores digests of each name token and its
3–12 character prefixes, so "raj" finds "Rajesh" through a plain indexed equality
probe with **no decryption at all**.

**Non-PII stays plaintext and indexed.** Place, stream, category, years of
experience, qualification and skills live in `candidate_profiles` / `skills` /
`candidate_skills` with trigram GIN indexes, powering fast multi-attribute fuzzy
filtering.

**Storage.** Every uploaded document — Aadhaar, PAN, Safety certificate, Medical,
PVC and Resume — goes to one **private** S3-compatible bucket (Supabase Storage,
Cloudflare R2 or MinIO) under an opaque, unguessable key. Nothing is public: the
dashboard fetches short-lived **presigned** download links on demand
(`PRESIGN_EXPIRY_SECONDS`, default 15 min). The unified intake uploads its six
documents concurrently, so the whole set costs about as much wall-clock as the
slowest single file. With no `S3_ENDPOINT_URL` configured the app falls back to a
local HMAC-signed filesystem backend, preserving the private + expiring-link
contract for zero-config local development.

## Local development

### 1. Backend

```bash
# Create the local Postgres database first (Homebrew Postgres example):
createdb gate_intake

cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set DB_USER / DB_PASSWORD and the two PII keys

python manage.py migrate
python manage.py seed_data      # seeds the 3 personas, projects, sample workers
python manage.py runserver      # http://localhost:8000
```

> **Zero-config escape hatch:** set `USE_SQLITE_FALLBACK=True` in `.env` to skip
> Postgres entirely. pgcrypto is Postgres-only, so on SQLite the PII layer
> transparently uses an equivalent in-process AES-256-GCM implementation — the
> blind index is byte-identical either way. Use Postgres for production.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env            # VITE_API_BASE_URL=http://localhost:8000/api
npm run dev                     # http://localhost:5173
```

## Tests

```bash
cd backend && pip install -r requirements-dev.txt && pytest      # 97 tests
cd frontend && npm test                                          # 36 tests
```

The backend suite is hermetic by construction — `core/settings_test.py` pins
in-memory SQLite, the local storage backend and the canned resume parser, so it
needs no Postgres, no bucket and no API keys. It covers the compliance lifecycle
(every gap classification and the expiry boundary), real-time gate decisions,
PII encrypt/decrypt + blind-index search, contractor-ownership scoping, the
unified intake, workforce demand, and the storage presign contract.

## API surface

| Method | Path | Persona | Purpose |
|---|---|---|---|
| POST | `/api/auth/login/` | any | email/password → token |
| GET | `/api/projects/` | all | role-scoped projects (read-only) |
| GET | `/api/projects/<id>/eligible-workers/` | Contractor | compliance split |
| POST | `/api/workforce-demand/` | Contractor | "3 Carpenters, 4 Masons" pool search |
| GET/POST | `/api/workers/` | Contractor | own worker registry |
| DELETE | `/api/workers/<id>/` | Contractor | remove worker + their stored files |
| GET | `/api/verification-status/` | Contractor | whole-pool verification matrix |
| POST | `/api/workers/bulk-upload/` | Contractor | CSV/Excel import (API only — no UI) |
| POST | `/api/intake/onboard-worker/` | Contractor | unified 6-document intake |
| POST | `/api/intake/verify-document/` | Contractor | commit one verified document |
| POST | `/api/intake/ocr-extract/` | Contractor | OCR a scan into form fields |
| POST | `/api/resume/parse/` | Contractor | parse a resume (preview or commit) |
| GET | `/api/candidates/search/` | Contractor | multi-attribute candidate filter |
| POST | `/api/storage/signed-url/` | Contractor / PE | batch of fresh expiring links |
| GET | `/api/trade-test/start/` | Contractor | 5 pictorial MCQs for the worker's trade |
| POST | `/api/trade-test/submit-attempt/` | Contractor | server-side scoring, 3-attempt lock |
| POST | `/api/safety-video/heartbeat/` | Contractor | monotonic watch progress |
| POST | `/api/documents/upload/` | Contractor | inline gap-fix upload |
| PATCH | `/api/documents/<id>/review/` | PE | verify/reject a document |
| GET/POST | `/api/intake-lists/` | Contractor/PE | list / submit |
| PATCH | `/api/intake-lists/<id>/review/` | PE | approve / request changes / reject |
| GET | `/api/gate-check/?aadhar=<n>` | Gate Security | **real-time** GREEN/RED decision |

## Deployment

Deploys **free on Render** (API + static frontend + managed Postgres) from the
`render.yaml` Blueprint. Full walkthrough is in **[DEPLOY.md](./DEPLOY.md)**. The
short version:

1. Create a **private** Supabase Storage bucket and an S3 access key for it.
2. Push this repo to GitHub.
3. Render → **New +** → **Blueprint** → select the repo. `render.yaml` provisions
   the Postgres database, the API and the static frontend, wires `DATABASE_URL`
   automatically, generates the two PII keys, and prompts for the storage
   credentials.
4. After the first deploy, confirm the two public URLs and (if Render appended a
   suffix to a service name) update `CORS_ALLOWED_ORIGINS` on the API and
   `VITE_API_BASE_URL` on the frontend, then redeploy.

Migrations + seeding run automatically on every deploy (see `render.yaml`'s
`startCommand`), so a fresh database is always populated with the dummy personas.

## Environment variables

**Backend** (`backend/.env.example`) — Django (`DJANGO_SECRET_KEY`,
`DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`), database
(`DATABASE_URL` or the individual `DB_*` vars, plus `USE_SQLITE_FALLBACK`), PII
(`PII_ENCRYPTION_KEY`, `PII_BLIND_INDEX_KEY` — required, must differ), storage
(`S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`,
`S3_REGION`, `S3_KEY_PREFIX`, `PRESIGN_EXPIRY_SECONDS`), resume parsing
(`RESUME_PARSER_PROVIDER`, `ANTHROPIC_API_KEY`/`GEMINI_API_KEY`,
`REQUIRE_RESUME_FOR_COMPLIANCE`) and OCR (`OCR_PROVIDER`, `OCRSPACE_API_KEY`).

**Frontend** (`frontend/.env.example`): `VITE_API_BASE_URL`.
