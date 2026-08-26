BEGIN;

CREATE TABLE IF NOT EXISTS trial_case_uses (
  case_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  used_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trial_case_uses_user
  ON trial_case_uses(user_id, used_at DESC);

COMMIT;
