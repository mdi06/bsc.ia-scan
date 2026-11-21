-- Real NFT holder counts (holders.py / GET /api/holders), so they survive API restarts and redeploys.
-- Run once in the Supabase SQL editor BEFORE deploying the backend that uses it.
--
-- A table of its own, not a field of projects.data: the worker rewrites a project's whole `data` from its own copy
-- on every save, which would put the discovery placeholder (holders: 1) back. Only the API writes this table.
-- Without it the API logs a warning once and keeps the counts in memory only (no errors, nothing lost but the
-- counts on a restart).

CREATE TABLE IF NOT EXISTS holder_counts (
    contract TEXT PRIMARY KEY,
    holders INTEGER NOT NULL,
    counted_at DOUBLE PRECISION NOT NULL
);

-- Written and read by the backend with the service role key (bypasses RLS); no public policy.
ALTER TABLE holder_counts ENABLE ROW LEVEL SECURITY;
