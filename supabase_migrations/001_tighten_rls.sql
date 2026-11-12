-- Tighten row-level security: remove public write access.
-- The backend must connect with the service role key (bypasses RLS).
-- Run manually in the Supabase SQL editor. Public SELECT policies are kept.

DROP POLICY IF EXISTS "Public upsert projects" ON projects;
DROP POLICY IF EXISTS "Public upsert user_favorites" ON user_favorites;
