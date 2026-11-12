"""Token blocklists shared by the API (feed/overview filtering) and monitor.py (profit metrics).

The frontend keeps its own copies in app.js (EXCLUDED_PEGS_SYMBOLS, EXCLUDED_PEGS_CONTRACTS,
SYNTHETIC_EQUITY_SYMBOLS); keep them in sync by hand.

Two tiers exist on purpose:
- *_CORE sets are the original, narrow lists. monitor.py uses them for post-mint profit metrics.
- The full sets (core + extras) are what the API uses to hide pegged/blue-chip/synthetic assets.
Whether monitor.py should also use the full sets is a product decision.
"""

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
