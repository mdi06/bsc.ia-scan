-- ==============================================================================
-- Supabase Schema for Agent ID Monitor (Railway API + Supabase)
-- ==============================================================================
-- Run this in your Supabase SQL Editor (Dashboard -> SQL Editor -> New Query)

-- 1. Projects Table (Token Registrations, Smart Money Drops, Security Audits)
CREATE TABLE IF NOT EXISTS projects (
    contract TEXT PRIMARY KEY,
    name TEXT,
    symbol TEXT,
    token_id TEXT,
    block BIGINT DEFAULT 0,
    holders INTEGER DEFAULT 0,
    risk_level TEXT DEFAULT 'SAFE',
    risk_badge TEXT DEFAULT '🟢 Clean',
    sniper_ready BOOLEAN DEFAULT FALSE,
    is_proxy_clone BOOLEAN DEFAULT FALSE,
    score REAL DEFAULT 0.0,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Bulky fields that rarely change (mint calldata, GoPlus report, ...); written only when they change.
    -- db.py rebuilds a record as static_data + data. See supabase_migrations/002_static_data.sql.
    static_data JSONB,
    detected_at TEXT,
    updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);

-- Indexes for lightning-fast filtered & sorted queries
CREATE INDEX IF NOT EXISTS idx_projects_block ON projects(block DESC);
CREATE INDEX IF NOT EXISTS idx_projects_sniper ON projects(sniper_ready);
CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC);

-- 2. User Favorites Table (Multi-Wallet Web3 Bookmarks)
CREATE TABLE IF NOT EXISTS user_favorites (
    wallet TEXT PRIMARY KEY,
    favorites JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);

-- Real NFT holder counts, written by the API (supabase_migrations/003_holder_counts.sql)
CREATE TABLE IF NOT EXISTS holder_counts (
    contract TEXT PRIMARY KEY,
    holders INTEGER NOT NULL,
    counted_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE holder_counts ENABLE ROW LEVEL SECURITY;

-- Small named values the app keeps for itself (the Agent ID wallet counter); supabase_migrations/005_kv_stats.sql
CREATE TABLE IF NOT EXISTS kv_stats (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);
ALTER TABLE kv_stats ENABLE ROW LEVEL SECURITY;

-- 3. Row Level Security (RLS) Policies
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_favorites ENABLE ROW LEVEL SECURITY;

-- Allow public read access to projects
CREATE POLICY "Public read projects" ON projects
    FOR SELECT USING (true);

-- Writes to projects are performed by the backend with the service role key,
-- which bypasses RLS. No public write policy is defined on purpose.

-- Allow public read of favorites; writes go through the backend (service role)
CREATE POLICY "Public read user_favorites" ON user_favorites
    FOR SELECT USING (true);

