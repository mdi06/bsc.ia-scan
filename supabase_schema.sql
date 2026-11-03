-- ==============================================================================
-- Supabase Schema for Agent ID Monitor (Render Free + Supabase)
-- ==============================================================================
-- Run this in your Supabase SQL Editor (Dashboard -> SQL Editor -> New Query)

-- 1. Projects Table (Token Registrations, Smart Money Drops, Security Audits)
CREATE TABLE IF NOT EXISTS projects (
    contract TEXT PRIMARY KEY,
    name TEXT,
    symbol TEXT,
    token_id TEXT,
    block BIGINT DEFAULT 0,
    progress REAL DEFAULT 0.0,
    holders INTEGER DEFAULT 0,
    status TEXT DEFAULT 'TRACKING',
    risk_level TEXT DEFAULT 'SAFE',
    risk_badge TEXT DEFAULT '🟢 Clean',
    sniper_ready BOOLEAN DEFAULT FALSE,
    is_proxy_clone BOOLEAN DEFAULT FALSE,
    score REAL DEFAULT 0.0,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    detected_at TEXT,
    updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);

-- Indexes for lightning-fast filtered & sorted queries
CREATE INDEX IF NOT EXISTS idx_projects_block ON projects(block DESC);
CREATE INDEX IF NOT EXISTS idx_projects_progress ON projects(progress DESC);
CREATE INDEX IF NOT EXISTS idx_projects_sniper ON projects(sniper_ready);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC);

-- 2. User Favorites Table (Multi-Wallet Web3 Bookmarks)
CREATE TABLE IF NOT EXISTS user_favorites (
    wallet TEXT PRIMARY KEY,
    favorites JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);

-- 3. Row Level Security (RLS) Policies
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_favorites ENABLE ROW LEVEL SECURITY;

-- Allow public read access to projects
CREATE POLICY "Public read projects" ON projects
    FOR SELECT USING (true);

-- Allow service_role and backend to insert / update projects
CREATE POLICY "Public upsert projects" ON projects
    FOR ALL USING (true) WITH CHECK (true);

-- Allow public read & write to favorites (authenticated by wallet in request)
CREATE POLICY "Public read user_favorites" ON user_favorites
    FOR SELECT USING (true);

CREATE POLICY "Public upsert user_favorites" ON user_favorites
    FOR ALL USING (true) WITH CHECK (true);

