"""Token blocklists and the asset rules shared by the API (feed/overview filtering) and monitor.py (profit metrics).

The frontend keeps its own copies in app.js (EXCLUDED_PEGS_SYMBOLS, EXCLUDED_PEGS_CONTRACTS, BOGUS_SCAM_CONTRACTS,
SYNTHETIC_EQUITY_SYMBOLS, EXCLUDED_NAME_PHRASES and the functions isExcludedMajorAsset, getSanitizedMcap,
isSecondaryMarketAsset). test_parity_python_js.py runs both implementations over the same records and fails when
they drift, so change the two together.

Two tiers exist for historical reasons:
- *_CORE sets are the original, narrow lists.
- The full sets (core + extras) are what both the API and monitor.py's profit metrics use to hide
  pegged/blue-chip/synthetic assets (is_excluded_asset).
"""
import re
import time
from typing import Optional

# Contracts removed from the API outright (also the default targets of POST /api/admin/purge-bogus).
BOGUS_SCAM_CONTRACTS = [
    "0xd0523ce816e2b404db5633d181572cded33d7fa9",  # PrinterAi ($100T fake price on $0.0002 LP)
    "0xc5a5f6337bd9d1ee1a7367a804fce07094956922",  # The Bank ($99.7K fake price on $0.00 LP)
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # Binance-Peg ETH (Layer 1 crypto)
    "0x7425889fe94f9d693e8daefe88bcced6acfef4c0",  # Meta Platforms (METAB)
    "0xbe9d156892e55e7154bcd3cb0fea677f9d3103e1",  # SpaceX (SPCXB)
    "0x80106cb3ead06659a5ad19df39d9b4733863b9b0",  # Microsoft (MSFTB)
    "0x5b1910eaad6450e50f816082aa078c41f10c292f",  # Tesla, Inc. (TSLAB)
    "0x431a6136ee1b2f76378c8a14b53ef11ba9099b7a",  # Apple (AAPLB)
    "0x360d0b00c3c3a9d70e6e7372cfbe85e68b95973c",  # DogeZilla (legacy meme clone with $0 mcap / sub-decimal display bug)
    "0xb9e1fd5a02d3a33b25a14d661414e6ed6954a721",  # SOON Token (existing token; added via a smart wallet's transfer)
    "0x2f8a339b5889ffac4c5a956787cda593b3c36867",  # CoinMarketCap 20 Index DTF (CMC20; index product, not an agent)
]
BOGUS_SET = {c.lower() for c in BOGUS_SCAM_CONTRACTS}

# Pegged / blue-chip contracts (BTCB, WBNB, USDT, USDC, BUSD, ETH, DAI, CAKE). Same list for API and monitor.
EXCLUDED_PEGS_CONTRACTS = {
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",  # BTCB
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
    "0x55d398326f99059ff775485246999027b3197955",  # USDT
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
    "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # ETH (Binance-Peg)
    "0x14016e85a25a55f2e084741d69d9d82361f38e2b",  # DAI
    "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",  # CAKE
}

PEG_SYMBOLS_CORE = {
    "BTCB", "WBTC", "BTC", "ETH", "WETH", "WBNB", "BNB", "USDT", "USDC", "BUSD",
    "DAI", "TUSD", "FDUSD", "CAKE", "U", "OSK", "FIST",
}
EXCLUDED_PEGS_SYMBOLS = PEG_SYMBOLS_CORE | {
    "SOL", "XRP", "ADA", "DOGE", "DOT", "AVAX", "MATIC", "POL", "LINK", "LTC", "SHIB", "UNI",
    "ZEC", "BCH", "XLM", "EOS", "ATOM", "TRX", "FIL", "NEAR", "XMR", "ETC", "APT", "SUI", "PEPE", "FLOKI", "DOGEZILLA",
    # Other dollar stablecoins: not agent mints, and their multi-billion caps would top every valuation stat
    "USD1", "USDE", "USDD", "USDS", "USD0", "FRAX", "LUSD", "PYUSD", "USDP", "GUSD",
}

EQUITY_SYMBOLS_CORE = {
    "TSLAB", "AAPLB", "MSFTB", "METAB", "SPCXB", "NVDA", "NVDAB", "AMZNB", "GOOGLB", "MRNAB", "SOXSB", "CRCLB", "QQQB", "MSTRB",
}
SYNTHETIC_EQUITY_SYMBOLS = EQUITY_SYMBOLS_CORE | {
    "TSLA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "MRNA", "SOXS", "SOXL", "SOXLB",
    "QQQ", "TQQQ", "TQQQB", "MSTR", "BABA", "BABAB", "BNCB", "SKHYB",
    "COIN", "COINB", "PLTR", "PLTRB", "AMD", "AMDB", "SPY", "SPYB", "TSMC", "TSMB", "PDD", "PDDB",
    "NFLX", "NFLXB", "INTC", "INTCB", "DIS", "DISB", "UBER", "UBERB", "BA", "BAAB",
}


# --------------------------------------------------------------------------------------------------------------
# Shared asset rules. app.js mirrors each of these line by line (see the module docstring).

EXCLUDED_NATIVE_PLACEHOLDER = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
EXCLUDED_CONTRACTS_ALL = frozenset(EXCLUDED_PEGS_CONTRACTS) | BOGUS_SET | {EXCLUDED_NATIVE_PLACEHOLDER}

# Matched as whole words / phrases (not substrings): "ETF" must not hit "Rocketfi".
EXCLUDED_NAME_PHRASES = (
    "ETF", "PROSHARES", "DIREXION", "BEAR 3X", "BULL 3X", "INVESQO", "INVESCO",
    "BSTOCKS", "TOKENIZED STOCK", "S&P 500", "NASDAQ", "ISHARES", "VANGUARD",
    "PANCAKESWAP", "BINANCE-PEG", "WRAPPED BNB", "TETHER USD", "USD COIN", "WORLD LIBERTY", "ZCASH", "DOGEZILLA",
)
_EXCLUDED_NAME_RE = re.compile(
    "|".join("(?<![A-Z0-9])" + re.escape(p) + "(?![A-Z0-9])" for p in EXCLUDED_NAME_PHRASES)
)


def name_is_excluded(name) -> bool:
    return bool(_EXCLUDED_NAME_RE.search(str(name or "").upper().strip()))


def is_excluded_asset(d) -> bool:
    """Pegged / blue-chip / synthetic-equity / known-scam asset, by contract, symbol or name."""
    if not d or not isinstance(d, dict):
        return True
    c = str(d.get("contract") or d.get("address") or "").lower().strip()
    if c in EXCLUDED_CONTRACTS_ALL:
        return True
    sym = str(d.get("symbol") or d.get("token_symbol") or "").upper().strip()
    if sym in EXCLUDED_PEGS_SYMBOLS or sym in SYNTHETIC_EQUITY_SYMBOLS:
        return True
    return name_is_excluded(d.get("name"))


# Market-cap sanity guard: an (unreliable) market cap above this with less liquidity behind it is not shown.
MCAP_GUARD_ABOVE_USD = 50_000_000
MCAP_GUARD_LP_BELOW_USD = 250_000


def _num(v) -> float:
    try:
        f = float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and f not in (float("inf"), float("-inf")) else 0.0


def sanitized_mcap(d):
    """The market cap to show: the record's market_cap (or fdv), else supply x price for a BEP-20 token whose
    supply unit is certain (never a magnitude guess); None when unknown, excluded, or an implausible valuation
    (above $50M with under $250K liquidity). One rule for Python and JavaScript."""
    if not d or not isinstance(d, dict) or is_excluded_asset(d):
        return None
    mc = _num(d.get("market_cap")) or _num(d.get("fdv"))
    if mc <= 0:
        price = _num(d.get("token_price_usd")) or _num(d.get("market_price_usd"))
        supply = _num(d.get("total_minted")) or _num(d.get("max_supply"))
        if price > 0 and supply > 0 and "BEP-20" in str(d.get("standard") or ""):
            mc = supply * price
    if mc <= 0:
        return None
    if mc > MCAP_GUARD_ABOVE_USD and _num(d.get("lp_usd")) < MCAP_GUARD_LP_BELOW_USD:
        return None
    return mc


def is_secondary_market(d) -> bool:
    """Trades on a DEX / has Element sales: the SECONDARY tab and /api/feed?filter=secondary."""
    if not d or not isinstance(d, dict) or is_excluded_asset(d):
        return False
    lp = _num(d.get("lp_usd"))
    mc = sanitized_mcap(d) or 0.0
    px = _num(d.get("token_price_usd"))
    vol = _num(d.get("volume_24h"))
    sec_vol = _num(d.get("secondary_vol_bnb"))
    has_pair = bool(d.get("pair_address") or d.get("pair_url"))
    return lp > 50 or mc > 500 or (px > 0 and vol > 0) or sec_vol > 0 or has_pair or bool(d.get("is_graduated"))


# --- FREE tab: free, not sold out, not paused, not unsafe, mint window open. Mirrors app.js isFreeMintableNow and
# its helpers (isFreeMintDrop, isPreMintedToken, isDropSoldOut, isDropUnsafeToMint, getMintTerms); the parity test
# runs both. The browser shows only the newest feed page, so the server picks these out of the whole list.
_NFT_STANDARD_RE = re.compile(r"721|1155|NFT")


def _js_number(v) -> float:
    """JavaScript's Number(v) for a JSON value: null -> 0, booleans -> 0 / 1, numeric strings parsed, else NaN."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0.0
        low = s.lower()
        try:
            if low.startswith("0x"):
                return float(int(s, 16))
            if "inf" in low or "nan" in low or "_" in s:
                return float("nan")
            return float(s)
        except ValueError:
            return float("nan")
    return float("nan")


def _uncompressed(d, key):
    """d[key] without unpacking a compact record's compressed fields (compact.CompactRecord.hot_get): `security`,
    `is_paused` and `sold_sel` are cold there, and decoding every record on each snapshot build is what compact.py
    exists to avoid. A cold field reads as None, which only ever lets a drop through; the browser applies the full
    rule (isFreeMintableNow) to every row it shows, and the list view keeps those three fields. Plain dicts read
    normally."""
    hot_get = getattr(d, "hot_get", None)
    return hot_get(key) if hot_get else d.get(key)


def _js_or0(v):
    """JavaScript's `v || 0`."""
    return v if v else 0


def is_free_mint(d) -> bool:
    if not d:
        return False
    p_str = str(d.get("mint_price_str") or "").upper()
    if d.get("has_free_mint") or "FREE" in p_str:
        return True
    return (_js_number(_js_or0(d.get("mint_value_wei"))) == 0 and _js_number(_js_or0(d.get("mint_price_bnb"))) == 0
            and "PAID" not in p_str and "DEX" not in p_str and "TBD" not in p_str)


_DECIMALS_IN_STANDARD_RE = re.compile(r"\((\d+) decimals\)")


def is_mint_count_cap(d) -> bool:
    """True when the stored max_supply is under one whole token: not a supply, but a COUNT of mint slots that
    monitor.read_supply scaled by 10**decimals as if it were a token amount."""
    ms = _js_number(d.get("max_supply")) if d else 0.0
    return ms == ms and 0 < ms < 1


def mint_slot_count(d) -> int:
    """How many mint slots the contract has, or 0 when unknown: the stored `mint_slots` (new records), else the slot count
    hidden in a stored sub-1 max_supply (records written before `mint_slots` existed)."""
    if not d:
        return 0
    slots = _js_number(d.get("mint_slots"))
    if slots == slots and slots >= 1:
        return int(slots)
    if is_mint_count_cap(d):
        m = _DECIMALS_IN_STANDARD_RE.search(str(d.get("standard") or ""))
        return int(round(_js_number(d.get("max_supply")) * 10 ** (int(m.group(1)) if m else 18)))
    return 0


def corrected_max_supply(d) -> float:
    """The cap in the units total_minted is in (whole tokens / items), or 0.0 when there is none or it is unknown.

    Fair-mint dispensers answer maxSupply() with a count of mint slots (ANTCO: 220) and totalMinted() with a count of
    mints (13), while totalSupply() is a token amount (1,040,000 = 13 x its 80,000 tokens per mint). read_supply took
    the token amount for total_minted but scaled the slot count by 10**18 for the cap: max_supply 2.2e-16, so 2,914
    drops (22% of the table) read as "sold out" and filled to 100%; only 3 of them really are.
    A cap under one whole token is such a count: rescale it (slots x tokens_per_mint) when tokens_per_mint is known,
    else the cap is unknown (0.0), never a made-up "full"."""
    ms = _js_number(d.get("max_supply")) if d else 0.0
    if ms == ms and ms >= 1:
        return ms if ms < 1e20 else 0.0
    # no real cap stored: a slot count (stored as `mint_slots`, or as a sub-1 max_supply by older records) times what one
    # mint gives, when that is known
    slots = mint_slot_count(d)
    per_mint = _js_number(d.get("tokens_per_mint")) if d else 0.0
    if slots >= 1 and per_mint == per_mint and per_mint > 0:
        return float(slots) * per_mint
    return 0.0


def _max_supply(d) -> float:
    return corrected_max_supply(d)


# "Crowd surge" is the second way a drop becomes "snipe ready": no cap is known (so there is no progress to read), but a lot of people
# are minting. It used to be `holders >= 100`, which for a token is every wallet holding it, not minters: 539 of the 683 drops that
# were snipe ready only this way were DEX-traded tokens with a pre-minted 1,000,000,000 supply and no mint at all.
CROWD_SURGE_MIN_MINTS = 100
CROWD_SURGE_ACTIVE_WINDOW_SEC = 24 * 3600


def crowd_surge_mints(d, now: Optional[float] = None) -> int:
    """How many mints a drop has when that counts as a crowd surge, else 0. All of these must hold:
    - no cap is known (with a cap, progress decides, not a crowd);
    - it is a real mint: not a DEX-traded token, and a token needs a known tokens_per_mint to count mints at all (a pre-minted
      supply of 1,000,000,000 is not 1,000,000,000 mints);
    - at least CROWD_SURGE_MIN_MINTS mints so far (NFT items, a fair-mint's own counter, or total / tokens-per-mint);
    - it is still minting: the mint probe says open, or a mint was seen within CROWD_SURGE_ACTIVE_WINDOW_SEC."""
    if not d or _max_supply(d) > 0:
        return 0
    if "DEX" in str(d.get("mint_price_str") or "").upper():
        return 0
    std = str(d.get("standard") or "")
    total = _js_number(_js_or0(d.get("total_minted")))
    if not (total == total and total > 0):
        return 0
    if _NFT_STANDARD_RE.search(std.upper()) or "FAIR-MINT" in std.upper():
        mints = total
    else:
        per_mint = _js_number(d.get("tokens_per_mint"))
        if not (per_mint == per_mint and per_mint > 0):
            return 0
        mints = total / per_mint
    terms = d.get("mint_terms") if isinstance(d.get("mint_terms"), dict) else {}
    last = _js_number(d.get("last_mint_at"))
    now = time.time() if now is None else now
    still_minting = terms.get("open") is True or (last == last and last > 0 and now - last <= CROWD_SURGE_ACTIVE_WINDOW_SEC)
    mints = int(mints)
    return mints if (still_minting and mints >= CROWD_SURGE_MIN_MINTS) else 0


def is_pre_minted_token(d) -> bool:
    if not d:
        return False
    ms = _max_supply(d)
    tm = _js_number(_js_or0(d.get("total_minted")))
    return (ms > 0 and tm == ms and not _NFT_STANDARD_RE.search(str(d.get("standard") or "").upper())
            and not _uncompressed(d, "sold_sel") and not d.get("cap")
            and not is_mint_count_cap(d))       # a dispenser whose slot count shows it is a mint, even a full one


def is_sold_out(d) -> bool:
    if not d:
        return False
    ms = _max_supply(d)
    tm = _js_number(_js_or0(d.get("total_minted")))
    if not (ms > 0) or is_pre_minted_token(d):
        return False
    return tm / ms >= 0.999


# Stats bar (app.js renderStats): a capped mint 50-99.9% filled is "momentum", 80%+ "near cap". Either one counts as
# active only while it is still being minted: the worker stamps last_mint_at whenever its minted count goes up.
NEAR_CAP_PCT = 80.0
MOMENTUM_PCT = 50.0
MINT_ACTIVE_WINDOW_SEC = 6 * 3600


def mint_progress_bucket(d) -> Optional[str]:
    """'sold_out' / 'near_cap' (80-99.9%) / 'momentum' (50-79.9%) / 'open' (under 50%) for a mint with a real cap, else
    None (no cap, or a token whose whole supply was minted up front: it was never a mint that could fill). Mirrors
    app.js mintProgressBucket."""
    if not d:
        return None
    ms = _max_supply(d)
    if not ms or is_pre_minted_token(d):
        return None
    tm = _js_number(_js_or0(d.get("total_minted")))
    pct = tm / ms * 100.0
    if pct >= 99.9:
        return "sold_out"
    if pct >= NEAR_CAP_PCT:
        return "near_cap"
    if pct >= MOMENTUM_PCT:
        return "momentum"
    return "open"


def is_minting_recently(d, now: Optional[float] = None) -> bool:
    at = _js_number(d.get("last_mint_at")) if d else 0
    if not (at == at and at > 0):
        return False
    return (time.time() if now is None else now) - at <= MINT_ACTIVE_WINDOW_SEC


def is_unsafe_to_mint(d) -> bool:
    if not d:
        return False
    level = str(d.get("risk_level") or "").upper()
    badge = str(d.get("risk_badge") or "").upper()
    if level in ("RUG", "HIGH") or "HONEYPOT" in badge or "DRAIN" in badge or d.get("is_honeypot"):
        return True
    security = _uncompressed(d, "security")
    return bool(isinstance(security, dict) and security.get("is_fund_drain"))


def _mint_window_open(d, now: float) -> bool:
    t = d.get("mint_terms") if isinstance(d.get("mint_terms"), dict) else {}

    def pos(v):
        n = _js_number(v)
        return n if (n == n and n not in (float("inf"), float("-inf")) and n > 0) else None

    start, end = pos(t.get("start")), pos(t.get("end"))
    if end is not None and end <= now:
        return False                       # ended
    if start is not None and start > now:
        return False                       # not started yet
    state = t.get("state")
    return not (t.get("open") is False or state in ("not_open", "ended", "sold_out"))


def is_free_mintable_now(d, now: Optional[float] = None) -> bool:
    """The FREE tab and /api/feed?filter=free_mintable."""
    if not d or not isinstance(d, dict):
        return False
    if not is_free_mint(d) or is_sold_out(d) or is_unsafe_to_mint(d) or _uncompressed(d, "is_paused"):
        return False
    return _mint_window_open(d, time.time() if now is None else now)


# Interest score bands (the worker's formula; analyze_market_interest calls the same function).
def interest_tier(score) -> str:
    s = _num(score)
    if s >= 80:
        return "SURGING"
    if s >= 50:
        return "HIGH"
    if s >= 25:
        return "MODERATE"
    return "EARLY"
