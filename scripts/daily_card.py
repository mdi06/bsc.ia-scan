#!/usr/bin/env python3
"""Daily "24h Pulse" stats card + ready-to-post caption, from the live backend.

Fetches /api/stats (its `pulse_24h` block), fills template-daily.html, renders a 1080x1080 PNG with headless
Chrome and writes a caption next to it:

    out/daily/pulse-YYYY-MM-DD.png
    out/daily/pulse-YYYY-MM-DD.txt     # caption for X / Telegram / Binance Square

    python3 scripts/daily_card.py                       # production backend
    python3 scripts/daily_card.py --base http://localhost:8080
    python3 scripts/daily_card.py --input stats.json    # offline: a saved /api/stats response
    python3 scripts/daily_card.py --no-png              # caption + HTML only (no Chrome needed)

Chrome is found automatically (Google Chrome / Brave / Chromium on macOS and Linux); override with CHROME_BIN.
Stdlib only. The fetch timeout is long because a cold Render instance takes ~35 s to answer.
"""

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "template-daily.html")
DEFAULT_BASE = "https://bsc-ia-scan.onrender.com"
DEFAULT_OUT = os.path.join(ROOT, "out", "daily")
SITE_URL = "https://agent-id-monitor.vercel.app"
TOP_ROWS = 5
NAME_MAX = 20

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
]


# ------------------------------------------------------------------------------------------------ data
def fetch_stats(base: str, timeout: float, retries: int = 2) -> dict:
    url = f"{base.rstrip('/')}/api/stats"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "daily-card/1.0"})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.load(res)
        except Exception as e:  # cold start / transient network error
            last = e
            if attempt < retries:
                time.sleep(5)
    raise SystemExit(f"could not fetch {url}: {last}")


def fetch_market(base: str, timeout: float, retries: int = 2) -> dict:
    """Market-wide totals over EVERY tracked asset, paged from /api/feed (500 per page, ~1.5 MB each).

    Only `volume_24h` is summed, and only for records whose market data is not stale. Deliberately NOT
    computed: buy/sell counts. `buys_24h` holds token amounts (up to 1e25) for ~10% of records, so any ratio
    built from it is meaningless."""
    seen, offset = {}, 0
    while True:
        url = f"{base.rstrip('/')}/api/feed?limit=500&offset={offset}"
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "daily-card/1.0"})
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    page = json.load(res)
                break
            except Exception as e:
                if attempt == retries:
                    raise SystemExit(f"could not fetch {url}: {e}")
                time.sleep(5)
        for p in page.get("projects") or []:
            if isinstance(p, dict) and p.get("contract"):
                seen[p["contract"]] = p
        if not page.get("has_more") or not page.get("projects"):
            break
        offset += 500
    live = [p for p in seen.values() if not p.get("market_stale")]
    volumes = [num(p.get("volume_24h")) for p in live]
    return {
        "tracked": len(seen),
        "total_volume": sum(volumes),
        "active": sum(1 for v in volumes if v > 0),
        "top_volume": max(volumes, default=0.0),
    }


def wallet_growth(state_path: str, today: str, wallets):
    """(delta, since_label) versus the most recent earlier day in the state file, within 3 days; else (None, None).
    Records today's count (same-day reruns overwrite). `wallets` None leaves the file alone."""
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    delta = since = None
    if wallets:
        today_ts = time.mktime(time.strptime(today, "%Y-%m-%d"))
        for day in sorted((d for d in state if d < today), reverse=True):
            age_days = (today_ts - time.mktime(time.strptime(day, "%Y-%m-%d"))) / 86400
            if age_days <= 3:
                delta = int(wallets) - int(state[day])
                since = time.strftime("%d %b", time.strptime(day, "%Y-%m-%d"))
            break
        state[today] = int(wallets)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(state.items())[-60:]), f, indent=1)
    return delta, since


def extract(payload: dict):
    """(stats, pulse) from either a /api/stats response or the bare stats object."""
    stats = payload.get("stats", payload) if isinstance(payload, dict) else {}
    pulse = stats.get("pulse_24h")
    if not isinstance(pulse, dict):
        raise SystemExit("response has no pulse_24h block (backend still warming up, or wrong URL?)")
    return stats, pulse


# ------------------------------------------------------------------------------------------- formatting
def num(v, default=0.0) -> float:
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else default
    except (TypeError, ValueError):
        return default


def fmt_int(v) -> str:
    return f"{int(round(num(v))):,}"


def fmt_money(v) -> str:
    x = num(v)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= limit:
            return f"${x / limit:.1f}{suffix}"
    return f"${x:,.0f}"


def fmt_pct(v):
    """('+897%', 'up') / ('-64%', 'down') / ('n/a', 'na')."""
    if v is None:
        return "n/a", "na"
    x = num(v, None)
    if x is None:
        return "n/a", "na"
    text = f"{x:+,.0f}%" if abs(x) >= 100 else f"{x:+.1f}%"
    return text, ("up" if x > 0 else "down" if x < 0 else "na")


def clip(s: str, n: int = NAME_MAX) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def cashtag(sym: str) -> str:
    """$SYM for plain-ASCII tickers; otherwise the bare symbol (a $ in front of CJK reads badly)."""
    sym = (sym or "").strip()
    return f"${sym}" if sym.isascii() and sym.isalnum() else sym


def top_launches(pulse: dict) -> list:
    rows = [r for r in (pulse.get("top_launches") or []) if isinstance(r, dict)]
    return sorted(rows, key=lambda r: num(r.get("volume_24h")), reverse=True)[:TOP_ROWS]


# ------------------------------------------------------------------------------------------------ card
def rows_html(launches: list) -> str:
    if not launches:
        return '<div class="empty">No launch cleared the liquidity + volume bar in this window.</div>'
    out = []
    for i, r in enumerate(launches, 1):
        pct, cls = fmt_pct(r.get("price_change_24h"))
        sym = html.escape(clip(r.get("symbol") or "", 12))
        out.append(
            f'<div class="row mono"><span class="rank">{i}</span>'
            f'<span class="nm">{html.escape(clip(r.get("name") or "Unknown"))}<small>{sym}</small></span>'
            f'<span class="vol">{fmt_money(r.get("volume_24h"))}</span>'
            f'<span class="chg {cls}">{html.escape(pct)}</span>'
            f'<span class="sm">🧠 {int(num(r.get("smart_count")))}</span></div>'
        )
    return "".join(out)


def hunter_text(pulse: dict) -> str:
    who, n = pulse.get("top_hunter"), int(num(pulse.get("top_hunter_count")))
    return f"🧠 Top wallet: {clip(str(who), 18)} · {n} drops" if who and n else "🧠 Smart-money tracking"


def growth_text(delta, since) -> str:
    return f"{delta:+,} since {since}" if delta is not None else "Binance Agentic Wallets"


def render_html(stats: dict, pulse: dict, day: str, market=None, growth=(None, None)) -> str:
    wallets = stats.get("agent_wallets")
    market = market or {}
    tokens = {
        "DATE": day.upper(),
        "DROPS": fmt_int(pulse.get("new_drops")),
        "GRADUATED": fmt_int(pulse.get("graduated")),
        "VOLUME": fmt_money(pulse.get("dex_volume_usd")),
        "SMART": fmt_int(pulse.get("smart_inflows")),
        "CLUSTERS": fmt_int(pulse.get("smart_clusters")),
        "THREATS": fmt_int(pulse.get("threats_blocked")),
        "CLEAN": fmt_int(pulse.get("clean_mints")),
        "TOTALVOL": fmt_money(market["total_volume"]) if market else "—",
        "ACTIVE": f"{fmt_int(market['active'])} active of {fmt_int(market['tracked'])} tracked" if market else "all tracked assets",
        "WALLETS": fmt_int(wallets) if wallets else "—",
        "WALLETSUB": growth_text(*growth),
        "HUNTER": hunter_text(pulse),
    }
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    for k, v in tokens.items():
        page = page.replace(f"__{k}__", html.escape(v))
    return page.replace("__ROWS__", rows_html(top_launches(pulse)))


def caption(pulse: dict, stats=None, market=None, growth=(None, None)) -> str:
    lines = [
        "BNB Chain Agent ID radar, last 24h:",
        "",
        f"• {fmt_int(pulse.get('new_drops'))} new drops ({fmt_int(pulse.get('graduated'))} reached a DEX)",
    ]
    if market:
        lines.append(f"• {fmt_money(market['total_volume'])} total DEX volume across {fmt_int(market['active'])} active tokens")
        lines.append(f"• {fmt_money(pulse.get('dex_volume_usd'))} of it from tokens launched in the last 24h")
    else:
        lines.append(f"• {fmt_money(pulse.get('dex_volume_usd'))} DEX volume from new launches")
    lines += [
        f"• {fmt_int(pulse.get('smart_inflows'))} smart-wallet inflows, {fmt_int(pulse.get('smart_clusters'))} clusters of 3+",
        f"• {fmt_int(pulse.get('threats_blocked'))} flagged as honeypot / rug-risk",
    ]
    wallets = (stats or {}).get("agent_wallets")
    if wallets:
        delta, since = growth
        lines.append(f"• {fmt_int(wallets)} agent wallets" + (f" ({delta:+,} since {since})" if delta is not None else ""))
    top = top_launches(pulse)
    if top:
        lines += ["", "Top by volume:"]
        for r in top:
            pct, _ = fmt_pct(r.get("price_change_24h"))
            lines.append(f"{cashtag(r.get('symbol') or r.get('name') or '?')}  {fmt_money(r.get('volume_24h'))} vol, {pct} 24h")
    lines += ["", f"Live radar: {SITE_URL}", "", "On-chain data, not financial advice. #BNBChain #BSC #AgentID"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------- render
def find_chrome():
    env = os.environ.get("CHROME_BIN")
    if env:
        return env if os.path.exists(env) or shutil.which(env) else None
    for c in CHROME_CANDIDATES:
        path = c if os.path.isabs(c) else shutil.which(c)
        if path and os.path.exists(path):
            return path
    return None


def screenshot(chrome: str, html_path: str, png_path: str) -> None:
    with tempfile.TemporaryDirectory() as profile:  # throwaway profile: never touches the user's browser
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
               f"--user-data-dir={profile}", "--force-device-scale-factor=1", "--window-size=1080,1080",
               "--virtual-time-budget=3000", f"--screenshot={png_path}", f"file://{html_path}"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if not os.path.exists(png_path) or os.path.getsize(png_path) == 0:
        raise SystemExit(f"Chrome did not produce a PNG:\n{res.stderr[-800:]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE, help="backend base URL")
    ap.add_argument("--input", help="read a saved /api/stats JSON instead of fetching")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--no-png", action="store_true", help="skip the Chrome render")
    ap.add_argument("--no-market", action="store_true", help="skip the ~27 MB market-wide totals fetch (tile shows —)")
    args = ap.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = fetch_stats(args.base, args.timeout)
    stats, pulse = extract(payload)
    market = None if (args.input or args.no_market) else fetch_market(args.base, args.timeout)

    today = time.strftime("%Y-%m-%d")
    stem = os.path.join(args.out, f"pulse-{today}")
    os.makedirs(args.out, exist_ok=True)
    day = time.strftime("%d %b %Y")
    # offline --input runs use sample data: never let them write wallet history
    growth = (None, None) if args.input else wallet_growth(os.path.join(args.out, "state.json"), today, stats.get("agent_wallets"))

    with open(stem + ".html", "w", encoding="utf-8") as f:
        f.write(render_html(stats, pulse, day, market, growth))
    with open(stem + ".txt", "w", encoding="utf-8") as f:
        f.write(caption(pulse, stats, market, growth))
    print(f"caption: {stem}.txt")

    if args.no_png:
        print(f"html:    {stem}.html")
        return 0
    chrome = find_chrome()
    if not chrome:
        print(f"no Chrome found (set CHROME_BIN); HTML is at {stem}.html", file=sys.stderr)
        return 1
    screenshot(chrome, os.path.abspath(stem + ".html"), stem + ".png")
    os.remove(stem + ".html")  # the PNG is the artifact; keep the HTML only with --no-png
    print(f"card:    {stem}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
