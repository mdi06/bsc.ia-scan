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
    "PANCAKESWAP", "BINANCE-PEG", "WRAPPED BNB", "TETHER USD", "USD COIN", "ZCASH", "DOGEZILLA",
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
