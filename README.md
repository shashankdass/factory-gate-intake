# Factory Gate-Intake Optimization

A digital factory **gate-intake** platform that compresses worker onboarding
verification from **~5 days to under 24 hours**. React SPA + Django REST Framework
+ PostgreSQL, structured as a single monorepo for a one-push free deploy to Render.

```
factory-gate-intake/
├── backend/        # Django REST Framework API + Postgres models + data seed
├── frontend/       # React (Vite) SPA with role-switching header
├── render.yaml     # Render Blueprint (API + static web + managed Postgres)
├── DEPLOY.md       # Step-by-step free Render deployment guide
└── .gitignore
```

## Personas & workflow

| Persona | Does | Dashboard |
|---|---|---|
| **Principal Employer (PE)** | Creates projects, defines mandatory requirements, reviews & approves/rejects submitted lists | `/employer` |
| **Contractor** | Views pre-assigned workers, filters instantly, fixes missing/expired docs inline, submits list | `/contractor` |
| **Field Officer** | Bulk-imports worker master profiles via CSV/Excel | `/field-officer` |
| **Gate Security** | Fast Aadhar lookup → GREEN/RED entry decision | `/gate` |

### Dummy test credentials (seeded automatically)

| Role | Email | Password |
|---|---|---|
| Principal Employer | `pe.admin@factory.com` | `pe_test_123` |
| Contractor | `contractor.one@vendor.com` | `contractor_test_123` |
| Field Officer | `field.officer@vendor.com` | `field_test_123` |
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

Only a **Verified, non-expired** document counts toward compliance. The
`/api/projects/<id>/eligible-workers/` endpoint uses this to split workers into
`ready_to_deploy` vs `needs_fixes` with the exact gap list per worker.

## Local development

### 1. Backend

```bash
# Create the local Postgres database first (Homebrew Postgres example):
createdb gate_intake

cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set DB_USER / DB_PASSWORD to match your Postgres

python manage.py migrate
python manage.py seed_data      # seeds the 4 personas, projects, sample workers
python manage.py runserver      # http://localhost:8000
```

> **Zero-config escape hatch:** set `USE_SQLITE_FALLBACK=True` in `.env` to skip
> Postgres entirely and use a local SQLite file. Leave it `False` (the default) for
> the real Postgres-backed setup.

The `.env.example` ships with sensible Postgres defaults. You can also run the raw
DDL directly instead of `migrate`:

```bash
psql -d gate_intake -f backend/sql/schema.sql
```

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env            # VITE_API_BASE_URL=http://localhost:8000/api
npm run dev                     # http://localhost:5173
```

## API surface

| Method | Path | Persona | Purpose |
|---|---|---|---|
| POST | `/api/auth/login/` | any | email/password → token |
| GET | `/api/projects/` | all | role-scoped projects |
| POST | `/api/projects/` | PE | create project |
| GET | `/api/projects/<id>/eligible-workers/` | Contractor | compliance split |
| POST | `/api/workers/bulk-upload/` | Field Officer | CSV/Excel import |
| POST | `/api/documents/upload/` | Contractor | inline doc upload |
| PATCH | `/api/documents/<id>/review/` | PE / Field Officer | verify/reject a doc |
| GET/POST | `/api/intake-lists/` | Contractor/PE | list / submit |
| PATCH | `/api/intake-lists/<id>/review/` | PE | approve / request changes / reject |
| GET | `/api/gate-check/?aadhar=<n>` | Gate Security | GREEN/RED decision |

## Deployment

Deploys **free on Render** (API + static frontend + managed Postgres) from the
`render.yaml` Blueprint. Full walkthrough with screenshots-worth of detail is in
**[DEPLOY.md](./DEPLOY.md)**. The short version:

1. Push this repo to GitHub.
2. Render → **New +** → **Blueprint** → select the repo. `render.yaml` provisions
   the Postgres database, the API, and the static frontend, and wires `DATABASE_URL`
   automatically.
3. After the first deploy, confirm the two public URLs and (if Render appended a
   suffix to a service name) update `CORS_ALLOWED_ORIGINS` on the API and
   `VITE_API_BASE_URL` on the frontend, then redeploy.

Migrations + seeding run automatically on every deploy (see `render.yaml`'s
`startCommand`), so a fresh database is always populated with the dummy personas.

## Environment variables

**Backend** (`backend/.env.example`): `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`,
`DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, and either `DATABASE_URL` (production /
Render) or the individual `DB_ENGINE/NAME/USER/PASSWORD/HOST/PORT` (local),
plus `USE_SQLITE_FALLBACK`.

**Frontend** (`frontend/.env.example`): `VITE_API_BASE_URL`.
