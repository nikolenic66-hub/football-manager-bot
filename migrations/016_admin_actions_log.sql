CREATE TABLE IF NOT EXISTS admin_actions_log(
 id BIGSERIAL PRIMARY KEY,
 admin_user_id BIGINT NOT NULL,
 action TEXT NOT NULL,
 target_type TEXT,
 target_id BIGINT,
 payload JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_actions_log_created ON admin_actions_log(created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_admin_actions_log_target ON admin_actions_log(target_type,target_id);
