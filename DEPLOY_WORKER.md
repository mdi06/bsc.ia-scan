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
   # /opt/agent-id-monitor is the agentid user's home (it already holds dotfiles), so the code goes in app/
   sudo -u agentid git clone https://github.com/mdi06/bsc.ia-scan.git /opt/agent-id-monitor/app
   cd /opt/agent-id-monitor/app
   sudo -u agentid python3 -m venv .venv
   sudo -u agentid .venv/bin/pip install -r requirements.txt
   ```
3. **Credentials.** Create `/opt/agent-id-monitor/app/.env.worker` (readable by `agentid` only, `chmod 600`) with the
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
cd /opt/agent-id-monitor/app && sudo -u agentid git pull && sudo -u agentid .venv/bin/pip install -q -r requirements.txt
sudo systemctl restart agent-id-worker
sudo journalctl -u agent-id-worker --since "3 min ago" --no-pager | grep -E "BSC Block|Error|Traceback" | tail
```
`git status` on the host always shows `agent_id_stats.json` as modified: the worker writes its Agent ID counter there.
Keep that copy (the pull leaves it alone unless the file changed upstream; if `git pull` refuses, copy it aside, `git
checkout agent_id_stats.json`, pull, and copy it back). A pull alone changes nothing that is running: restart the
service.
Render redeploys the API on its own from `main`; the worker host does not, so pull there after merging worker
changes.

## After a restart that includes the cap repair and the risk re-score

Both heal old records by themselves; there is no script to run.

- **Mint caps.** Fair-mint dispensers used to be stored with a cap of about 2e-16 (a count of mint slots scaled as a token
  amount), so they looked sold out and the sweeper had stopped tracking them. At the start of every sweeper cycle the worker now
  rewrites each such stored cap at once (no chain reads: slots x tokens-per-mint is arithmetic), so the wrong "sold out" is gone
  within the first cycle and the next 10-minute write carries it to the database. Look for
  `[🔧 Cap Repair] N records stored a mint-slot count as their cap: rewritten` in the log. Their minted totals then catch up in the
  sweeper's normal rotation (hours for the slowest lane). Tokens whose `tokens_per_mint` is not known yet keep their slot count in
  `mint_slots` and get a cap once the tokens-per-mint loop reads it. Nothing to configure.
- **Risk re-score** (`RISK_RESCORE` in `.env.worker`):
  - `dry-run` (the default): re-checks the records the old Serial Dev / Fund Drain rules flagged, 8 every 30 seconds, and
    **only logs** what would change. Watch it with
    `journalctl -u agent-id-worker -f | grep "Risk Rescore"`: lines like `was RUG 🚨 Serial Dev: RUG->MED (🟡 Med Risk)` and a
    running total. About 2,500 records, so a couple of hours.
  - `apply`: writes the new verdict (risk fields and the sniper flags only, never prices or liquidity) and stamps
    `risk_rules_v`. Set it when the dry-run log looks right, then `sudo systemctl restart agent-id-worker`.
  - `off`: the loop does not run. `RISK_RESCORE_BATCH` and `RISK_RESCORE_INTERVAL_SEC` change the pace.
  A record whose GoPlus audit cannot be repeated is skipped (and retried in 6 hours), never downgraded.

## Going back

Stop the worker on the host (`systemctl disable --now agent-id-worker`), then set `RUN_WORKER=1` on Render.
