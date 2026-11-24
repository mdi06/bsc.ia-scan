-- Deletes the fake wallet row that used to hold the Agent ID counter ('__system_agent_id_stats__' in user_favorites).
--
-- The counter lives in kv_stats since migration 005, and the code no longer reads or writes this row. Run it only AFTER the API and
-- the worker are on the version that dropped the fallback: until then an old process that finds kv_stats empty would still read it.
-- (kv_stats already holds the live counter, so nothing is lost.) It removes exactly one row.

DELETE FROM user_favorites WHERE wallet = '__system_agent_id_stats__';
