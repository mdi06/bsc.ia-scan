# Running the worker off Render

## Why

The worker scans BNB Smart Chain by downloading every new block with its transactions. On BSC that is about
30 KB per block (gzipped), with a block every ~0.45 s: roughly **0.25 GB an hour, ~180 GB a month** at full
coverage. Render's free tier allows 5 GB a month of bandwidth and counts these downloads, so the worker cannot run
there. The API and website stay on Render; the worker runs on a host with plenty of traffic. Both talk to the
same Supabase database, so nothing else changes.

```
 BSC RPC / DexScreener / GoPlus ──> worker (your host) ──> Supabase <── API (Render) <── browsers
```

## Pick a host

Anything that runs Python 3.10+ around the clock, with:

- **at least 1 GB RAM** (the worker keeps the full project list in memory);
- **at least ~250 GB a month of traffic, counting incoming traffic**. The block downloads are *incoming*;
  many VPS providers do not count or charge incoming traffic at all, which makes this cheap. Check the
  provider's current terms before choosing;
- a stable connection (a laptop that sleeps will miss blocks).

## Switch over, in this order

Never run two workers at once: both would scan and write the same records.

1. **Render: turn the worker off.** In the Render dashboard, service `agent-id-backend` → Environment, add
   `RUN_WORKER` = `0` and save (this redeploys). `render.yaml` sets the same value if the service is managed by a
   Blueprint. The logs should now show the API starting without "Starting all … autonomous discovery & sweeper
   loops".
2. **Set up the host** (Linux example, as root):
   ```bash
   useradd -r -m -d /opt/agent-id-monitor agentid
   sudo -u agentid git clone https://github.com/mdi06/bsc.ia-scan.git /opt/agent-id-monitor
   cd /opt/agent-id-monitor
   sudo -u agentid python3 -m venv .venv
   sudo -u agentid .venv/bin/pip install -r requirements.txt
   ```
3. **Credentials.** Create `/opt/agent-id-monitor/.env.worker` (readable by `agentid` only, `chmod 600`) with the
   same values the Render service has:
   ```
   SUPABASE_URL=...
   SUPABASE_KEY=...            # the service role key, as on Render
   ```
4. **Start it** with the systemd unit in `deploy/agent-id-worker.service`:
   ```bash
   cp deploy/agent-id-worker.service /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now agent-id-worker
   journalctl -u agent-id-worker -f
   ```
   You should see `[📡 BSC Block #…]` lines and, within a few minutes, `[📈 Market Engine] Synced …`.
   Without systemd: `python server.py --worker-only` (keep it running with `tmux`, `screen` or a process manager).
5. **Check the site.** New drops appear right away; price and liquidity changes reach the site within about
   10 minutes (`DB_SUPABASE_FLUSH_INTERVAL_SEC`, default 600). Keep it there: the Render API downloads every
   batch the worker writes, and those reads still count against Render's 5 GB.

## Updating

```bash
cd /opt/agent-id-monitor && sudo -u agentid git pull && sudo -u agentid .venv/bin/pip install -r requirements.txt
systemctl restart agent-id-worker
```
Render redeploys the API on its own from `main`; the worker host does not, so pull there after merging worker
changes.

## Going back

Stop the worker on the host (`systemctl disable --now agent-id-worker`), then set `RUN_WORKER=1` on Render.
