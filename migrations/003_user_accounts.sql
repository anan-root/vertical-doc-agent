CREATE TABLE IF NOT EXISTS user_accounts (
  account_id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL,
  department TEXT,
  phone TEXT,
  email TEXT,
  password_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  last_login_at TIMESTAMPTZ,
  metadata_json JSONB
);

ALTER TABLE user_accounts ADD COLUMN IF NOT EXISTS password_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_user_accounts_status ON user_accounts(status);
CREATE INDEX IF NOT EXISTS idx_user_accounts_role ON user_accounts(role);
CREATE INDEX IF NOT EXISTS idx_user_accounts_last_login ON user_accounts(last_login_at DESC);
