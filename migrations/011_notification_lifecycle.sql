ALTER TABLE notifications ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_notifications_active_delivery
  ON notifications(sent_at, cancelled_at, failed_at, deliver_at, id);
