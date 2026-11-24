-- Drops projects.progress and projects.status.
--
-- Both were written by the worker as constants (progress 0.0, status 'TRACKING' on every row) and never read: the API derives
-- the real progress and status from each record's own data (api.derive_progress / derive_status).
--
-- ORDER MATTERS. Run this only AFTER the API and the worker are on the version that no longer sends these two columns.
-- The old worker upserts them, and PostgREST rejects an unknown column. Check the worker log shows a normal flush first.
-- Dropping a column is not reversible, but nothing is lost: every row held the default.

DROP INDEX IF EXISTS idx_projects_progress;
DROP INDEX IF EXISTS idx_projects_status;
ALTER TABLE projects DROP COLUMN IF EXISTS progress;
ALTER TABLE projects DROP COLUMN IF EXISTS status;
