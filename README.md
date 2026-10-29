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
| **`↑` / `↓`** | **Navigate Rows** | Move the `▶` cursor up/down across detected projects |
| **`k` / `j`** or **`w` / `s`** | **Vim / Gamer Keys** | Alternative navigation keys for moving between rows |
| **`1` – `9`** | **Quick Jump** | Jump directly to row number `1`, `2`, `3`... |
| **`Enter`** or **`Space`** | **Deep-Dive Modal** | Expands full inspection card with reverse-engineering signals, full supply, & snipe command |
| **`r`** | **Instant Refresh** | Re-queries BSC on-chain for DEX LP reserves, token price, pause status, & minted supply |
| **`b`** | **Open in BscScan** | Opens the selected contract address directly in your macOS browser |
| **`q`** or **`ESC`** | **Exit / Close Modal** | Closes the modal or exits the monitor |

*(Tip: Pass `--plain` if you prefer scrolling raw logs instead of the interactive table).*

### 2. Refresh Metrics via CLI
Updates on-chain state for all tracked projects directly from the terminal:
```bash
python3 monitor.py refresh
```

### 3. View Snapshot Table
Prints the current table of tracked projects without launching the live block listener:
```bash
python3 monitor.py list
```

### 4. Deep-Inspect Any Contract Address
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
3. **DEX Liquidity & Capital Commitment**: Automatically resolves PancakeSwap V2 pools for associated tokens, extracting pool depth (WBNB/USD) and live implied token price.
4. **Mint Velocity & Supply Depletion**: Tracks `totalMinted()` vs. `MAX_SUPPLY()`, calculating the % claimed.
5. **Deployer Skin-in-the-Game**: Inspects the deployer wallet's BNB balance and transaction count (nonce).
- **Hype Tiers**: `❄️ COLD` (0-24) | `🌱 WARMING UP` (25-49) | `🔥 HIGH TRACTION` (50-74) | `🚀 VIRAL RUSH` (75-100)



