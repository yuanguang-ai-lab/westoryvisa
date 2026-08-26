BEGIN;

DROP TABLE IF EXISTS billing_webhook_events CASCADE;
DROP TABLE IF EXISTS billing_subscriptions CASCADE;
DROP TABLE IF EXISTS billing_refunds CASCADE;
DROP TABLE IF EXISTS payment_transactions CASCADE;
DROP TABLE IF EXISTS billing_orders CASCADE;
DROP TABLE IF EXISTS billing_products CASCADE;
DROP TABLE IF EXISTS trial_case_uses CASCADE;
DROP TABLE IF EXISTS intake_links CASCADE;
DROP TABLE IF EXISTS auth_sessions CASCADE;
DROP TABLE IF EXISTS email_verifications CASCADE;
DROP TABLE IF EXISTS app_session CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS review_issues CASCADE;
DROP TABLE IF EXISTS ds160_answers CASCADE;
DROP TABLE IF EXISTS field_evidence CASCADE;
DROP TABLE IF EXISTS ds160_fields CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS ds160_cases CASCADE;
DROP TABLE IF EXISTS clients CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;

CREATE TABLE organizations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  email TEXT UNIQUE,
  phone TEXT,
  password_hash TEXT,
  password_salt TEXT,
  password_iterations INTEGER NOT NULL DEFAULT 240000,
  user_key TEXT UNIQUE,
  role TEXT,
  email_verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE email_verifications (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  purpose TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  code_salt TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  send_status TEXT NOT NULL DEFAULT 'sent',
  provider TEXT,
  failure_reason TEXT,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE clients (
  id TEXT PRIMARY KEY,
  organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
  created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  record_key TEXT UNIQUE,
  full_name TEXT NOT NULL,
  passport_number TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE ds160_cases (
  id TEXT PRIMARY KEY,
  client_id TEXT REFERENCES clients(id) ON DELETE CASCADE,
  organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
  owner_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  owner_name TEXT,
  visa_type TEXT NOT NULL,
  status TEXT NOT NULL,
  current_step INTEGER NOT NULL DEFAULT 0,
  source_type TEXT,
  review_priority TEXT,
  notes TEXT,
  payload_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  slot TEXT NOT NULL,
  file_name TEXT,
  stored_path TEXT,
  mime_type TEXT,
  file_size BIGINT,
  sha256 TEXT,
  scan_status TEXT NOT NULL DEFAULT 'empty',
  scan_message TEXT,
  ocr_text TEXT,
  ocr_json JSONB,
  parser_name TEXT,
  parser_version TEXT,
  processed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE ds160_fields (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  field_key TEXT NOT NULL,
  section TEXT NOT NULL,
  label TEXT NOT NULL,
  value TEXT,
  source_document TEXT,
  source_document_id TEXT,
  source_page INTEGER,
  evidence_text TEXT,
  extraction_method TEXT,
  confidence DOUBLE PRECISION,
  risk_level TEXT,
  status TEXT NOT NULL,
  requires_user_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
  confirmed BOOLEAN NOT NULL DEFAULT FALSE,
  edited_by_user BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE field_evidence (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  field_key TEXT NOT NULL,
  document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
  page_number INTEGER,
  evidence_text TEXT,
  confidence DOUBLE PRECISION,
  extraction_method TEXT,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE ds160_answers (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL,
  section TEXT NOT NULL,
  label TEXT NOT NULL,
  answer_value TEXT,
  details_json JSONB NOT NULL,
  status TEXT NOT NULL,
  source TEXT,
  sensitive BOOLEAN NOT NULL DEFAULT FALSE,
  confirmed_by_user BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(case_id, question_id)
);

CREATE TABLE review_issues (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
  issue_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  message TEXT NOT NULL,
  requires_user_resolution BOOLEAN NOT NULL DEFAULT FALSE,
  resolved BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE audit_logs (
  id BIGSERIAL PRIMARY KEY,
  case_id TEXT REFERENCES ds160_cases(id) ON DELETE CASCADE,
  actor TEXT,
  action TEXT NOT NULL,
  payload_json JSONB,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE app_session (
  id TEXT PRIMARY KEY,
  payload_json JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE intake_links (
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

CREATE TABLE trial_case_uses (
  case_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  used_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE billing_products (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL,
  duration_days INTEGER NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE billing_orders (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  product_id TEXT NOT NULL REFERENCES billing_products(id),
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  provider TEXT NOT NULL,
  provider_checkout_id TEXT UNIQUE,
  provider_payment_id TEXT,
  checkout_url TEXT,
  paid_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE payment_transactions (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES billing_orders(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  provider_transaction_id TEXT,
  transaction_type TEXT NOT NULL,
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE billing_refunds (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES billing_orders(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requested_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  provider_refund_id TEXT,
  amount INTEGER NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE billing_subscriptions (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
  product_id TEXT REFERENCES billing_products(id),
  source_order_id TEXT REFERENCES billing_orders(id) ON DELETE SET NULL,
  status TEXT NOT NULL,
  starts_at TIMESTAMPTZ NOT NULL,
  current_period_end TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE billing_webhook_events (
  provider_event_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_clients_organization_id ON clients(organization_id);
CREATE INDEX idx_clients_created_by_user_id ON clients(created_by_user_id);
CREATE INDEX idx_cases_organization_id ON ds160_cases(organization_id);
CREATE INDEX idx_cases_client_id ON ds160_cases(client_id);
CREATE INDEX idx_cases_owner_user_id ON ds160_cases(owner_user_id);
CREATE INDEX idx_documents_case_id ON documents(case_id);
CREATE INDEX idx_fields_case_id ON ds160_fields(case_id);
CREATE INDEX idx_field_evidence_case_id ON field_evidence(case_id);
CREATE INDEX idx_field_evidence_document_id ON field_evidence(document_id);
CREATE INDEX idx_ds160_answers_case_id ON ds160_answers(case_id);
CREATE INDEX idx_review_issues_case_id ON review_issues(case_id);
CREATE INDEX idx_audit_logs_case_id ON audit_logs(case_id);
CREATE INDEX idx_auth_sessions_user_id ON auth_sessions(user_id);
CREATE INDEX idx_trial_case_uses_user ON trial_case_uses(user_id, used_at DESC);
CREATE INDEX idx_intake_links_case_id ON intake_links(case_id, created_at DESC);
CREATE INDEX idx_email_verifications_email_created ON email_verifications(email, purpose, created_at DESC);
CREATE UNIQUE INDEX idx_email_verifications_sending
  ON email_verifications(email, purpose) WHERE send_status = 'sending';
CREATE INDEX idx_billing_orders_org_created ON billing_orders(organization_id, created_at DESC);
CREATE INDEX idx_payment_transactions_order ON payment_transactions(order_id, created_at DESC);
CREATE INDEX idx_billing_refunds_order ON billing_refunds(order_id, created_at DESC);

COMMIT;
