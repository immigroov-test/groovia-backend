-- Configurable public careers page and admin-managed openings.
-- Safe to run repeatedly on an existing Supabase project.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS career_settings (
  id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  apply_url text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO career_settings (id, apply_url)
VALUES (1, '')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS career_roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  department text NOT NULL,
  department_order integer NOT NULL DEFAULT 0 CHECK (department_order >= 0),
  title text NOT NULL,
  summary text NOT NULL DEFAULT '',
  description text NOT NULL DEFAULT '',
  expectations text NOT NULL DEFAULT '',
  location text,
  employment_type text,
  sort_order integer NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS career_roles_public_idx
  ON career_roles(is_active, department_order, sort_order);

ALTER TABLE career_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE career_roles ENABLE ROW LEVEL SECURITY;

-- Reads and writes intentionally go through FastAPI. Its public endpoint returns only
-- active roles, while every mutation is protected by require_admin.
DROP POLICY IF EXISTS career_settings_direct_read ON career_settings;
DROP POLICY IF EXISTS career_roles_direct_read ON career_roles;
