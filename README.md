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

The radar inspects the bytecode of every new BSC contract deployment and flags those that reference the Agent ID registry, often in the same block they are deployed. It combines that discovery feed with on-chain and market data in one web dashboard.

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

### HTTP API

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
Tests live in `tests/` (Python `test_*.py`, JavaScript `test_*.js`). Install the pinned dependencies first, then run both suites; GitHub runs the same steps on every pull request (`.github/workflows/test.yml`).
```bash
python3 -m pip install -r requirements-dev.txt && npm ci
python3 -m pytest
for t in tests/test_*.js; do node $t > /dev/null 2>&1 || echo "FAIL $t"; done
```
Run them without Supabase keys in the environment, so `db.py` uses local SQLite. One-off maintenance scripts (cleanups, the Supabase migration, the refactor parity check) live in `scripts/`.

---

## Disclaimer
BNB Agent Radar is an informational tool. Nothing it shows is financial advice. New contracts can be malicious even when an automated audit passes, so always check a contract yourself before interacting with it.
