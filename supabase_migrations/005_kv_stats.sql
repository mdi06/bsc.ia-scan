-- kv_stats: small named values the app keeps for itself, apart from user data.
-- First tenant: the Agent ID wallet counter (key 'agent_id_stats'), which lived in a fake wallet row of user_favorites,
-- a table anyone could write to through the favourites endpoint.
--
-- Safe to run before or after deploying the code that uses it: until the table exists, db.py logs one warning and keeps
-- reading and writing the old row. Run it, then the counter moves on the next write (within a minute of the worker's next flush).
--
-- The old row ('__system_agent_id_stats__' in user_favorites) is left in place for one release as the fallback; a later
-- migration deletes it.

CREATE TABLE IF NOT EXISTS kv_stats (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);

-- Copy the current counter so the API never reads an empty table
INSERT INTO kv_stats (key, value, updated_at)
SELECT 'agent_id_stats', favorites->0, COALESCE(updated_at, EXTRACT(EPOCH FROM NOW())::BIGINT)
FROM user_favorites
WHERE wallet = '__system_agent_id_stats__'
  AND jsonb_typeof(favorites) = 'array'
  AND jsonb_typeof(favorites->0) = 'object'
ON CONFLICT (key) DO NOTHING;

-- Read and written by the backend with the service role key (bypasses RLS); no public policy.
ALTER TABLE kv_stats ENABLE ROW LEVEL SECURITY;
