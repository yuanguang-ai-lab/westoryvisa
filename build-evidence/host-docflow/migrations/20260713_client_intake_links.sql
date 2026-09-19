BEGIN;

CREATE TABLE IF NOT EXISTS intake_links (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending',
  expires_at TIMESTAMPTZ NOT NULL,
  submitted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intake_links_case_id
  ON intake_links(case_id, created_at DESC);

COMMIT;
