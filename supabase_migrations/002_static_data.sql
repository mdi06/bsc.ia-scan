-- Bandwidth: keep the bulky, rarely changing fields of a project (mint_calldata, security, risk_flags, signals,
-- tx_hash, market_url, deployer) in their own column, so the frequent market updates no longer resend them.
-- Run once in the Supabase SQL editor BEFORE deploying the backend that uses it.
--
-- Safe to run on a live database: it only adds a nullable column. Existing rows keep everything in `data`
-- until the backend rewrites them; db.py reads both shapes. Without this column the backend logs a warning
-- once and keeps writing full records (no data loss, just no saving).

ALTER TABLE projects ADD COLUMN IF NOT EXISTS static_data JSONB;
