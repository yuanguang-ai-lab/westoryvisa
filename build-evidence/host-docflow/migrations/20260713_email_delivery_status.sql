BEGIN;

ALTER TABLE email_verifications
  ADD COLUMN IF NOT EXISTS send_status TEXT NOT NULL DEFAULT 'sent';

ALTER TABLE email_verifications
  ADD COLUMN IF NOT EXISTS provider TEXT;

ALTER TABLE email_verifications
  ADD COLUMN IF NOT EXISTS failure_reason TEXT;

UPDATE email_verifications
SET send_status = 'sent'
WHERE send_status IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_email_verifications_sending
  ON email_verifications(email, purpose)
  WHERE send_status = 'sending';

COMMIT;
