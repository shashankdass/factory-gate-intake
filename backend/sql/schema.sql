-- ============================================================================
--  Factory Gate-Intake Optimization — PostgreSQL DDL
--
--  This is the canonical relational schema. The Django models in
--  intake/models.py map 1:1 onto these tables (via db_table = ...), so you can
--  either run `manage.py migrate` (recommended) OR apply this script directly.
--
--  Run against an empty database, e.g.:
--     createdb gate_intake
--     psql -d gate_intake -f schema.sql
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Users (personas). Mirrors intake.User (a custom AbstractUser).
-- Only the app-specific columns are shown; Django adds the standard auth cols.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intake_user (
    id            BIGSERIAL PRIMARY KEY,
    password      VARCHAR(128) NOT NULL,
    last_login    TIMESTAMPTZ,
    is_superuser  BOOLEAN      NOT NULL DEFAULT FALSE,
    username      VARCHAR(150) NOT NULL UNIQUE,
    first_name    VARCHAR(150) NOT NULL DEFAULT '',
    last_name     VARCHAR(150) NOT NULL DEFAULT '',
    is_staff      BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    date_joined   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    email         VARCHAR(254) NOT NULL UNIQUE,
    role          VARCHAR(20)  NOT NULL,
    organization  VARCHAR(150) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_user_role ON intake_user (role);

-- ---------------------------------------------------------------------------
-- Requirements catalogue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requirements_master (
    id           BIGSERIAL PRIMARY KEY,
    name         VARCHAR(120) NOT NULL UNIQUE,
    description  VARCHAR(255) NOT NULL DEFAULT '',
    is_expirable BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id                     BIGSERIAL PRIMARY KEY,
    name                   VARCHAR(180) NOT NULL,
    description            TEXT         NOT NULL DEFAULT '',
    principal_employer_id  BIGINT       NOT NULL
        REFERENCES intake_user (id) ON DELETE CASCADE,
    is_active              BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_project_active ON projects (is_active);

-- PE ⇄ Contractor assignment (Django ManyToMany join table)
CREATE TABLE IF NOT EXISTS projects_contractors (
    id          BIGSERIAL PRIMARY KEY,
    project_id  BIGINT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES intake_user (id) ON DELETE CASCADE,
    UNIQUE (project_id, user_id)
);

-- ---------------------------------------------------------------------------
-- Project ⇄ Requirement junction
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_requirements (
    id             BIGSERIAL PRIMARY KEY,
    project_id     BIGINT  NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    requirement_id BIGINT  NOT NULL REFERENCES requirements_master (id) ON DELETE CASCADE,
    is_mandatory   BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (project_id, requirement_id)
);

-- ---------------------------------------------------------------------------
-- Workers  (aadhar_number UNIQUE prevents duplicate master profiles)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workers (
    id             BIGSERIAL PRIMARY KEY,
    name           VARCHAR(150) NOT NULL,
    skill_type     VARCHAR(100) NOT NULL,
    aadhar_number  VARCHAR(12)  NOT NULL UNIQUE,
    status         VARCHAR(12)  NOT NULL DEFAULT 'ACTIVE',
    contractor_id  BIGINT       REFERENCES intake_user (id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_worker_skill ON workers (skill_type);
CREATE INDEX IF NOT EXISTS idx_worker_contractor_skill ON workers (contractor_id, skill_type);

-- ---------------------------------------------------------------------------
-- Worker documents (verification lifecycle + expiry)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS worker_documents (
    id                  BIGSERIAL PRIMARY KEY,
    worker_id           BIGINT       NOT NULL REFERENCES workers (id) ON DELETE CASCADE,
    requirement_id      BIGINT       NOT NULL REFERENCES requirements_master (id) ON DELETE CASCADE,
    document_number     VARCHAR(120) NOT NULL DEFAULT '',
    document_file       VARCHAR(100) NOT NULL DEFAULT '',
    file_url            VARCHAR(500) NOT NULL DEFAULT '',
    verification_status VARCHAR(10)  NOT NULL DEFAULT 'Pending',
    expiry_date         DATE,
    rejection_reason    VARCHAR(255) NOT NULL DEFAULT '',
    uploaded_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_doc_worker_req ON worker_documents (worker_id, requirement_id);
CREATE INDEX IF NOT EXISTS idx_doc_status ON worker_documents (verification_status);

-- ---------------------------------------------------------------------------
-- Intake (deployment) lists
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intake_lists (
    id            BIGSERIAL PRIMARY KEY,
    project_id    BIGINT      NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    contractor_id BIGINT      NOT NULL REFERENCES intake_user (id) ON DELETE CASCADE,
    status        VARCHAR(20) NOT NULL DEFAULT 'Draft',
    pe_comments   TEXT        NOT NULL DEFAULT '',
    submitted_at  TIMESTAMPTZ,
    reviewed_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_list_lookup ON intake_lists (project_id, contractor_id, status);

-- ---------------------------------------------------------------------------
-- Intake list ⇄ Worker junction (unique per list to prevent overlaps)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intake_list_workers (
    id             BIGSERIAL PRIMARY KEY,
    intake_list_id BIGINT NOT NULL REFERENCES intake_lists (id) ON DELETE CASCADE,
    worker_id      BIGINT NOT NULL REFERENCES workers (id) ON DELETE CASCADE,
    UNIQUE (intake_list_id, worker_id)
);
