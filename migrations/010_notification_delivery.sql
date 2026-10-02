ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_attempts INT NOT NULL DEFAULT 0 CHECK(delivery_attempts>=0);
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS processing_at TIMESTAMPTZ;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS last_error TEXT;
CREATE INDEX IF NOT EXISTS idx_notifications_due_retry ON notifications(sent_at,next_attempt_at,deliver_at,processing_at,id);
