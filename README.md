<div align="center">

# BNB Agent Radar

**Real-time discovery and analytics for the BNB Chain Agent ID ecosystem.**

[Live App](https://agent-id-monitor.vercel.app/) · [Features](#features) · [Architecture](#architecture) · [Getting Started](#getting-started) · [API](#http-api)

</div>

---

## Overview

BNB Agent Radar watches BNB Smart Chain block by block and surfaces every new project built for **Agent ID** (`.ai`) holders: fair-mints, NFT collections and tokens that read the Agent ID registry or launch through Agent ID launchpads.

For each project it detects, the radar adds the context a holder needs to decide whether it is worth attention: mint progress, DEX liquidity and price, NFT floor and volume, holder distribution, a contract safety audit, and inflows from a curated set of smart wallets. It also tracks the growth of the Agent ID ecosystem itself, with a live count of Agent IDs minted on-chain.

### The problem

Projects that gate access to Agent ID holders are usually deployed as standalone contracts, not on standard meme launchpads. They do not appear on launchpad feeds or token lists, so holders find out about them late, through social media, after the mint is over. There is no single place that shows what has been built for Agent ID and how it is performing.

### The solution

The radar inspects the bytecode of every new BSC contract deployment and flags those that reference the Agent ID registry, often in the same block they are deployed. It combines that discovery feed with on-chain and market data in one dashboard, available on the web and in a terminal UI.

---

## Agent ID integration

The radar reads these BNB Smart Chain contracts:

| Contract | Address | Used for |
| :--- | :--- | :--- |
| Agent ID Registry (`.ai`) | [`0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F`](https://bscscan.com/address/0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F) | Detecting contracts that gate on Agent ID; counting Agent ID mints (`Transfer` from the zero address) |
| SPACE ID Master Registry (`.bnb`) | [`0x08CEd32a7f3eeC915Ba84415e9C07a7286977956`](https://bscscan.com/address/0x08CEd32a7f3eeC915Ba84415e9C07a7286977956) | Name resolution |
| Four.meme Agent ID Factory | [`0x5c952063c7fc8610FFDB798152D69F0B9550762b`](https://bscscan.com/address/0x5c952063c7fc8610FFDB798152D69F0B9550762b) | Tracking Agent ID launchpad deployments |
| Four.meme Router | [`0xF251F83e40a78868FcfA3FA4599Dad6494E46034`](https://bscscan.com/address/0xF251F83e40a78868FcfA3FA4599Dad6494E46034) | Tracking launchpad trading activity |

BNB Agent Radar is an independent project and is not affiliated with Binance, SPACE ID or Four.meme.

---

## Features

### Live discovery feed
- Scans every new BSC block and flags contract deployments that reference the Agent ID registry.
- Tracks Agent ID launchpad drops and clone-style deployments from proxy factories.
- Classifies each contract on-chain:
  - **ERC-721** via ERC-165 interface `0x80ac58cd` or `tokenURI(uint256)`
  - **ERC-1155** via ERC-165 interface `0xd9b67a26`
  - **BEP-20** via a valid `decimals()` return value
  - **Fair-mint contracts** that dispense an underlying BEP-20 through `token()`

### Project analytics
| Signal | Source |
| :--- | :--- |
| Mint progress | `totalMinted()` vs `MAX_SUPPLY()`, refreshed every 15 seconds for active drops |
| DEX liquidity and price | PancakeSwap V2 pools and DexScreener, refreshed every 2 minutes |
| NFT floor and volume | Element Market |
| Holders | GoPlus Security and contract state |
| Buy pressure | 1h and 24h buy counts and net flow |
| Deployer profile | Deployer BNB balance and transaction count |
| Safety audit | Honeypot check, buy/sell taxes, pause state |

Each project gets a 0–100 traction score, shown as **Cold**, **Warming Up**, **High Traction** or **Viral Rush**.

### Smart-wallet intelligence
- Monitors roughly 5,000 curated wallets and highlights projects they are minting or buying, separating free (gas-only) mints from paid accumulation.
- A leaderboard ranks the wallets by realised performance.

### Ecosystem metrics
- Live count of Agent IDs minted on the registry.
- **24h Pulse**: a daily summary of top launches, top NFT mints and ecosystem activity.

### Web app
- Wallet connection for MetaMask, Binance Web3 Wallet and Rabby (EIP-1193 / EIP-6963), plus a watch-only mode for any public address.
- Portfolio view across all tracked contracts using batched JSON-RPC, with BNB balance, USD value and price-based alerts.
- Favourites bound to the connected wallet and synced across devices, with a QR code for mobile.
- Customisable filter tabs (drag-and-drop or the Reorder dialog), saved in the browser.

### Terminal UI
A keyboard-driven terminal dashboard with the same data: live radar, launchpads, smart-wallet activity, leaderboard and portfolio.

### Safety by design
The radar never signs transactions or spends funds on its own. Any on-chain action is generated as a preview command (`baw contract-call preview`) for the user to simulate first, and needs explicit confirmation.

---

## Architecture

```
               BNB Smart Chain (RPC)          DexScreener · PancakeSwap · Element · GoPlus
                       │                                        │
                       ▼                                        ▼
            ┌──────────────────────────────────────────────────────────┐
            │  worker.py  (ingestion)                                  │
            │  block scanner · progress sweeper · market updater ·     │
            │  NFT sweeper · factory watcher · Agent ID mint counter   │
            └──────────────────────────────┬───────────────────────────┘
                                           │ db.py
                                           ▼
                           Supabase (Postgres) · SQLite fallback
                                           │
                                           ▼
            ┌──────────────────────────────────────────────────────────┐
            │  api.py / server.py  (FastAPI, read side)                │
            └───────────────┬──────────────────────────┬───────────────┘
                            ▼                          ▼
                  Web app (Vercel)              Terminal UI (tui.py)
                  index.html · app.js
```

| Component | Role | Hosting |
| :--- | :--- | :--- |
| `worker.py` | Scans blocks and persists drops, market data and Agent ID counts | Dedicated host (see [`DEPLOY_WORKER.md`](DEPLOY_WORKER.md)) |
| `api.py`, `server.py` | FastAPI read API with ETag caching and delta feeds | Render |
| `db.py` | Storage layer: Supabase with a local SQLite fallback | — |
| `index.html`, `app.js`, `style.css` | Web client, built and fingerprinted by `scripts/build.js` | Vercel |
| `tui.py`, `monitor.py` | Terminal UI and shared on-chain logic | Local |

---

## Getting started

### Requirements
- Python 3.10+
- Node.js 24 (only for building the web frontend)

### Install
```bash
git clone https://github.com/mdi06/bsc.ia-scan.git
cd bsc.ia-scan
pip install -r requirements-cli.txt   # API + terminal UI
```

### Run everything locally
Starts the scanner and the API, and serves the web app at `http://localhost:8080`:
```bash
./start_local.sh
```

### Configuration
All variables are optional for local use; without Supabase the app uses SQLite.

| Variable | Purpose |
| :--- | :--- |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase project used for storage |
| `RUN_WORKER` | `1` to run the scanner in the same process as the API, `0` for API only |
| `PORT` | API port (default `8080`) |
| `ADMIN_TOKEN` | Protects admin endpoints |
| `DB_PATH` | Location of the SQLite fallback database |

---

## Terminal UI

```bash
python3 tui.py live       # live block listener with the interactive dashboard
python3 tui.py explore    # browse projects already tracked
```

| Command | Description |
| :--- | :--- |
| `python3 tui.py scan-wallets [--blocks N]` | Scan recent blocks for smart-wallet activity (default 200 blocks) |
| `python3 tui.py sync-wallets` | Refresh the tracked wallet list |
| `python3 tui.py holdings` | Show your NFT and token balances across tracked contracts |
| `python3 tui.py refresh` | Refresh on-chain metrics for all projects |
| `python3 tui.py list` | Print a snapshot table without starting the listener |
| `python3 tui.py inspect <address>` | Analyse any contract: type, liquidity, price, mint velocity, score |

Add `--plain` for scrolling logs instead of the interactive table.

<details>
<summary>Keyboard shortcuts</summary>

| Key | Action |
| :--- | :--- |
| `Tab` / `p`, `1`–`4` | Switch tabs |
| `↑` `↓` / `k` `j` | Move between rows |
| `PgUp` `PgDn` / `[` `]`, `Home` `End` | Scroll by page, jump to top or bottom |
| `Enter` / `Space` | Open the project detail view (deployer trace, holders, audit) |
| `s` | Simulate a mint, then confirm before anything is broadcast |
| `m` / `b` | Open the market page / BscScan |
| `w` / `x` | Open the project website / X profile |
| `r` | Refresh on-chain data |
| `g` | Show only projects with smart-wallet inflow |
| `f` `l` `a` `c` | Filter: free mints, DEX liquidity, active, clear |
| `o` | Cycle sort: newest, smart money, score, velocity, progress, safety |
| `q` / `Esc` | Close the detail view or exit |

</details>

---

## HTTP API

| Endpoint | Description |
| :--- | :--- |
| `GET /api/feed` | Paginated project feed, with filters and delta updates (`since_block`) |
| `GET /api/stats` | Totals, Agent ID mint count and latest block |
| `GET /api/projects` | Project list with sorting |
| `GET /api/top-gainers` | Best-performing projects |
| `GET /api/smart-drops` | Projects with smart-wallet activity |
| `GET /api/smart-hunters` | Smart-wallet leaderboard |
| `GET /api/market-data`, `GET /api/bnb-price` | Market data and BNB price |
| `GET`/`POST /api/favorites` | Wallet-bound watchlists |
| `GET /health` | Health check |

---

## Frontend maintenance

### Refresh the bundled feed snapshot
The web app renders `feed_snapshot.json` first, and falls back to it while the backend is cold-starting, retrying the live feed after 5, 15 and 30 seconds. Refresh the snapshot before a frontend deploy so the first paint is recent:
```bash
python3 scripts/refresh_snapshot.py
```
It fetches `/api/feed?limit=150` from the production backend (`--base http://localhost:8080` for a local one) and writes the `projects` array to `feed_snapshot.json`. Commit the updated file with the deploy.

### Build and caching
Vercel runs `npm run build` (`scripts/build.js`) and serves `dist/`. `app.js` and `style.css` are minified with esbuild and written as content-hashed `assets/app.<hash>.js` and `assets/style.<hash>.css`, which `dist/index.html` loads. `vercel.json` caches `/assets/*` for a year (`immutable`) and keeps `index.html` revalidating, so each deploy is picked up on the next page load. Local development (`start_local.sh` / `server.py`) serves the unminified files and needs no build. To check a build locally:
```bash
npm run build && npm run check:minified
```

---

## Testing
```bash
python3 -m pytest
```

---

## Disclaimer
BNB Agent Radar is an informational tool. Nothing it shows is financial advice. New contracts can be malicious even when an automated audit passes, so always check a contract yourself before interacting with it.
