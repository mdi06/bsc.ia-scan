# Binance .ai Agent ID Real-Time Contract Monitor

Monitors the BNB Smart Chain (BSC) block-by-block to catch new fair-mints, NFT drops, and token projects that integrate with the official **Space ID / Binance `.ai` Agent ID Registry**:
- **Registry Address**: `0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F`

---

## Why This Works

Projects like **Jager ($JAGENT)** and **Oathbots ($OATH)** are not launched on standard meme launchpads. They are deployed as independent smart contracts that gate access to Binance Agentic Wallets by reading `0x60B9...022F`.

This monitor inspects the bytecode of every new contract deployment on BSC at **Block 0**. When a deployment references the `.ai` registry, the monitor:
1. Sounds an audio alert (chime on macOS).
2. Extracts the contract address, deployer, and block.
3. Checks if the contract offers a free mint for your wallet (`nimus.ai`).
4. Generates an instant `baw contract-call` command ready to simulate and snipe the mint.

---

## Installation

```bash
git clone https://github.com/mdi06/bsc.ia-scan.git
cd bsc.ia-scan
pip install -r requirements.txt
```

---

## Usage

### 1. Interactive Explorer & Live Radar
Launches an interactive TUI dashboard. Navigate through detected drops, view full on-chain metrics, and trigger instant snipes:
```bash
# Explore and navigate existing tracked projects:
python3 monitor.py explore

# Or run the live block-by-block listener with interactive dashboard:
python3 monitor.py live
```

#### ⌨️ Interactive Controls:
| Key | Action | Description |
| :--- | :--- | :--- |
| **`TAB`** or **`p`** | **Switch Tabs** | Cycle through **[1: 📡 Live Radar]** ➔ **[2: 🧠 Smart Alpha]** ➔ **[3: 🎒 My Portfolio]** |
| **`1`** / **`2`** / **`3`** | **Direct Tab Jump** | Jump directly to **1** (Live Radar), **2** (Smart Money Alpha), or **3** (My Portfolio) |
| **`↑` / `↓`** or **`k` / `j`** | **Navigate Rows** | Move the `▶` cursor up/down with dynamic sliding-window viewport scrolling |
| **`PgUp` / `PgDn`** or **`[` / `]`** | **Fast Page Scroll** | Scroll an entire screen of drops up or down |
| **`Home` / `End`** | **Jump to Ends** | Jump instantly to the very top or bottom of the project list |
| **`s`** | **1-Click Sniper** | Instant on-chain dry-run simulation & 1-click broadcast gate (`[Y/Enter]` confirm) |
| **`Enter`** or **`Space`** | **Deep-Dive Modal** | Expands full inspection card with reverse-engineering signals, full supply, holdings & audit |
| **`m`** | **Open Market** | Opens the collection on **Element NFT Market** or token on **PancakeSwap** in your browser |
| **`b`** | **Open in BscScan** | Opens the selected contract address or your holdings token page directly in BscScan |
| **`w`** / **`x`** | **Website / Twitter** | Opens the official website or Twitter/X page for the selected project |
| **`r`** | **Instant Refresh** | Re-queries BSC on-chain for DEX LP reserves, prices, minted supply & wallet balances |
| **`g`** | **Smart Money Filter** | Toggle filtering by drops with verified Smart Wallet inflow (`🧠 5k Tracked Wallets`) |
| **`f` / `l` / `a` / `c`** | **Filters** | Filter by Free mints (`f`), DEX LP (`l`), Active (`a`), or Reset all (`c`) |
| **`o`** | **Cycle Sort** | Toggle sorting between Newest, Smart Money, Heat Score, Velocity, Progress, and Safety |
| **`q`** or **`ESC`** | **Exit / Close Modal** | Closes the modal or exits the monitor |

*(Tip: Pass `--plain` if you prefer scrolling raw logs instead of the interactive table).*

### 2. 🧠 Smart Money Alpha Scanner (5,000 Tracked Wallets)
Scans recent blocks on BSC across ~5,000 verified smart agent wallets (curated by @OGlamozda) to detect early mints, insider accumulation, and alpha trends before they hit secondary markets:
```bash
# Scan recent 200 blocks (~10 minutes of BSC history):
python3 monitor.py scan-wallets

# Scan custom block depth (e.g. last 500 blocks / ~25 minutes):
python3 monitor.py scan-wallets --blocks 500

# Sync / refresh tracked wallet registry from Google Sheets:
python3 monitor.py sync-wallets
```

#### What It Detects:
- ⭐ **FREE MINTS (Gas Only - 0 BNB)**: Detects when smart wallets call mint functions with 0 BNB value (e.g. *Gondolux*, *Cometra*, *Drone Atlas*).
- 💎 **PAID TRENDS (>0 BNB - Heavy Inflow)**: Detects smart wallets spending real BNB to accumulate newly deployed tokens or fair-mint allocations (e.g. *TOSH*, *Hash21*, *Hallowed Agents*).
- **Automatic Audit**: Verifies honeypot status, buy/sell taxes, contract pause state, and current mint progress.
- 🛡️ **STRICT NO-AUTO-MINT POLICY**: The scanner **never automatically spends user funds or signs transactions**. All detected drops provide an exact `baw contract-call preview` simulation command, and are automatically indexed into your Live Radar table where you can review them and snipe with 1-click explicit confirmation (`[s]`).

### 3. View Wallet Portfolio & Holdings
Check your wallet's NFT & token balances across all tracked contracts directly via CLI:
```bash
python3 monitor.py holdings
# or alias:
python3 monitor.py my-nfts
```

### 4. Refresh Metrics via CLI
Updates on-chain state for all tracked projects directly from the terminal:
```bash
python3 monitor.py refresh
```

### 5. View Snapshot Table
Prints the current table of tracked projects without launching the live block listener:
```bash
python3 monitor.py list
```

### 6. Deep-Inspect Any Contract Address
Reverse-engineers any contract, calculating DEX liquidity, implied token price, mint velocity, and interest score:
```bash
python3 monitor.py inspect <contract_address>
```

### 4. Asset Classification (NFT vs. Token)
The monitor automatically analyzes contract interfaces on-chain to determine whether a newly detected project is an **NFT** or a **Fungible Token**:
- **🎨 ERC-721 (NFT Collection)**: Verified via ERC-165 interface id `0x80ac58cd` or `tokenURI(uint256)`. (e.g. *Oathbots*)
- **🖼️ ERC-1155 (Multi-Token NFT)**: Verified via ERC-165 interface id `0xd9b67a26`.
- **🪙 BEP-20 (Fungible Token)**: Verified via `decimals()` return value `0 <= dec <= 36`. (e.g. *$JAGENT token*)
- **⚡ Fair-Mint Contract**: Detects mint contracts that dispense an underlying BEP-20 token via `token()` (e.g. *Jager Mint Contract*).

### 5. On-Chain Interest & Traction Engine
To measure market interest and distinguish between empty hype and real commitment, the monitor runs multi-dimensional reverse-engineering checks:
1. **On-Chain Holders Base**: Resolves verified on-chain wallet holder count via GoPlus Security API (`holder_count`) and smart contract state.
2. **Real-Time Purchase Velocity**: Resolves 24h & 1h buy transaction volume (`buys_24h` / `buys_1h`) and net buy pressure via DexScreener DEX feeds.
3. **DEX Liquidity & Collection Trading Volume (`LP / Vol`)**:
   - **BEP-20 Tokens**: Automatically resolves PancakeSwap V2 pools for associated tokens, extracting pool depth (WBNB/USD) and live implied token price (e.g. `$14,105`).
   - **NFT Collections**: Displays real-time secondary marketplace trading volume denominated in BNB (e.g. `0.74 BNB` for *Oathbots* on Element Market). Unlisted / non-traded NFT mints cleanly display `—`.
4. **Secondary Market & Floor Price Engine**: Tracks secondary trading prices in real-time under the **`Floor / Trade`** column:
   - **NFT Floor Prices**: Displays live secondary market floor prices in BNB and USD (e.g. `0.00016 BNB ($0.11)` on Element Market).
   - **DEX Token Prices**: Displays real-time PancakeSwap DEX prices (e.g. `$0.000071` for $BNBS, `$0.0517` for $MARSID).
   - **Pre-Market / Primary Mints**: Displays `—` for projects still minting that haven't established secondary liquidity yet.
5. **Mint Velocity & Supply Depletion**: Tracks `totalMinted()` vs. `MAX_SUPPLY()`, calculating the % claimed.
6. **Deployer Skin-in-the-Game**: Inspects the deployer wallet's BNB balance and transaction count (nonce).
- **Hype Tiers**: `❄️ COLD` (0-24) | `🌱 WARMING UP` (25-49) | `🔥 HIGH TRACTION` (50-74) | `🚀 VIRAL RUSH` (75-100)



