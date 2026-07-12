# Deploying to Render (free)

This deploys three things on Render's **free** tier from one repo:

| Component | Render service type | Public? |
|---|---|---|
| PostgreSQL database | Managed Postgres | no (internal) |
| Django REST API | Web Service (Python) | yes |
| React frontend | Static Site | yes ← **testers open this** |

Everything is described in [`render.yaml`](./render.yaml), so Render provisions it
all in one shot as a **Blueprint**.

> **Free-tier caveats (important, but fine for testing):**
> - The API **sleeps after ~15 min idle** and takes ~50s to wake on the next
>   request. The first click after a quiet period is slow; after that it's fast.
> - The **free Postgres database is time-limited** (Render currently expires free
>   databases after ~30 days). Note the expiry date Render shows you; when it
>   lapses, create a new free DB and redeploy.
> - Uploaded document **files are ephemeral** (wiped on restart). The seeded data
>   uses `file_url` links so document previews still work regardless.

---

## Step 0 — Push the repo to GitHub

From the project root:

```bash
git init
git add -A
git commit -m "Factory gate-intake platform"
git branch -M main
# Create an empty repo on github.com first (no README), then:
git remote add origin https://github.com/<you>/factory-gate-intake.git
git push -u origin main
```

(Or use the GitHub CLI: `gh repo create factory-gate-intake --public --source=. --push`.)

---

## Step 1 — Create the Blueprint on Render

1. Sign in at <https://dashboard.render.com> (free account, no card needed for
   free services).
2. **New +** → **Blueprint**.
3. Connect your GitHub and pick the `factory-gate-intake` repo.
4. Render reads `render.yaml` and shows a plan:
   - `gate-intake-db` (Postgres, free)
   - `gate-intake-api` (web service, free)
   - `gate-intake-web` (static site, free)
5. Click **Apply**. Render provisions the DB, then builds the API and frontend.

The API's `startCommand` runs `migrate` + `seed_data` automatically, so the dummy
personas and sample data are created on first boot.

---

## Step 2 — Confirm the URLs (one-time)

`render.yaml` assumes these hostnames:

- API → `https://gate-intake-api.onrender.com`
- Web → `https://gate-intake-web.onrender.com`

If those service names were globally taken, Render appends a random suffix. Check
each service's real URL in the dashboard. **If either differs from the above:**

1. On **gate-intake-api** → *Environment* → set
   `CORS_ALLOWED_ORIGINS` to the real frontend URL.
2. On **gate-intake-web** → *Environment* → set
   `VITE_API_BASE_URL` to the real API URL **+ `/api`**.
3. Redeploy the affected service (frontend must rebuild since the API URL is baked
   into the bundle at build time).

If the names came through as-is, no changes are needed.

---

## Step 3 — Smoke-test

1. Open the **frontend URL**. (First load may take ~50s while the API wakes.)
2. It should land on the Principal Employer dashboard.
3. Use the header **role-switcher** to move between the four personas — no login
   needed, they're seeded.
4. Quick end-to-end check:
   - **Contractor** → project *Plant-A* → *Fix Requirements* shows gaps; submit a list.
   - **Principal Employer** → approve the list.
   - **Gate Security** → enter `100000000001` → **GREEN**; `100000000005` → **RED**.

Direct API health check:

```bash
curl -X POST https://<api-url>/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email":"gate.security@factory.com","password":"gate_test_123"}'
```

Should return a token + user JSON.

---

## Dummy test credentials (seeded)

| Role | Email | Password |
|---|---|---|
| Principal Employer | `pe.admin@factory.com` | `pe_test_123` |
| Contractor | `contractor.one@vendor.com` | `contractor_test_123` |
| Field Officer | `field.officer@vendor.com` | `field_test_123` |
| Gate Security | `gate.security@factory.com` | `gate_test_123` |

Testers can ignore these entirely and just use the role-switcher — the credentials
are only needed for the raw API or the Django admin (`/admin`).

---

## Re-seeding / resetting data

Data resets are safe to trigger by redeploying the API (seed is idempotent). To
wipe and repopulate from scratch, delete + recreate the `gate-intake-db` database
in the Render dashboard, then redeploy the API.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Frontend loads but every action errors / CORS error in console | `VITE_API_BASE_URL` (frontend) or `CORS_ALLOWED_ORIGINS` (API) doesn't match the real URLs — see Step 2. |
| `DisallowedHost` in API logs | Add the host to `DJANGO_ALLOWED_HOSTS`; `.onrender.com` should already cover it. |
| First request very slow | Expected — free web service was asleep. Subsequent requests are fast. |
| API build fails on `psycopg2` | Ensure `PYTHON_VERSION=3.12.6` is set (it is, in `render.yaml`). |
| DB connection refused after a few weeks | Free Postgres expired — create a new free DB, point `DATABASE_URL` at it, redeploy. |
