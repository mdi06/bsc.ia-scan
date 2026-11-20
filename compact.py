"""Compact in-memory project records for the API.

The API keeps every project record in RAM (`DatabaseManager._projects_cache`). A record is ~56 keys, and most of its
bytes are fields that no list view reads: the GoPlus `security` report, `sniper_checklist`, `hold` / `holder_distribution`,
`risk_flags`, `signals`, `tx_hash`, `dev_root`, `deployer` and (for a few contracts, up to 98 KB) `mint_calldata`.
Python's per-object overhead makes them ~50% of the memory. A CompactRecord keeps the hot fields in the dict itself
and the cold ones as one zlib-compressed JSON blob, decoded only when something reads them.

It behaves like the full dict (`get`, `[]`, `in`, iteration, `items()`, `json.dumps`, `dict(rec)`, `==` all see every
field, in the original key order), so no caller has to know about it. Reading a cold field decodes the blob (about
40 us on a laptop, a few hundred us on Render's 0.1 CPU); code that walks every record should use `hot_get`
or avoid cold fields. Nothing here talks to the database: no extra Supabase or Render bandwidth.
"""
import json
import sys
import zlib
from typing import Any, Dict, Iterator, Optional

# Fields no list view, filter, sort or counter reads: only the token modal does (they are served in full records) or
# nothing does. They are the bulky ones (security report, checklist, ...) and the rarely set ones. Fewer keys in the dict
# also matter: a dict of 42 keys or fewer has a table half the size of one with 43-85 keys. Everything else stays hot.
COLD_FIELDS = frozenset({
    "security", "sniper_checklist", "hold", "holder_distribution", "risk_flags", "signals",
    "tx_hash", "dev_root", "deployer", "token_price_bnb", "sniper_status", "classification",
    "icon", "rating", "sniper_badge", "is_paused", "cluster_n", "cluster_devs", "mint_cost_usd", "secondary_price_usd",
    "nft_surge_badge", "nft_surge_signal", "secondary_transfers", "holders_estimate", "market_source", "proxy_type",
    "master_implementation", "is_factory", "factory_badge", "sniper_ready_alerted", "sweep_at", "sold_sel",
    "supply_corrected_from", "floor_price_str", "volume_bnb", "total_volume_bnb", "user_balance", "lp_wbnb",
    "linked_token", "linked_pair_url", "linked_price_usd", "linked_lp_usd", "linked_fdv", "linked_market_cap",
    "linked_volume_24h", "linked_lp_pool",
})
# mint_calldata is cold only when it is long: most records carry a short one that list responses send as it is.
CALLDATA_KEY = "mint_calldata"
CALLDATA_COLD_MIN_CHARS = 512
# String values of these keys repeat across thousands of records (a few dozen distinct values): store one copy.
INTERN_KEYS = frozenset({
    "rating", "icon", "risk_level", "risk_badge", "sniper_badge", "mint_price_str", "mint_value_wei", "standard",
    "nft_surge_badge", "status", "chain", "dex",
})

_MISSING = object()
_ORDER_POOL: Dict[tuple, tuple] = {}     # one tuple per distinct key order (nearly every record has the same one)
_NO_COLD = b""

_dict_get = dict.get
_dict_contains = dict.__contains__
_dict_getitem = dict.__getitem__


DECODES = 0     # how many blobs were decoded (for tests and measurements)


def _decode(blob: bytes) -> Dict[str, Any]:
    global DECODES
    DECODES += 1
    return json.loads(zlib.decompress(blob))


class CompactRecord(dict):
    """A project record whose cold fields live in a compressed blob. See the module docstring."""
    __slots__ = ("_cold", "_order")

    # ---- reading a cold field decodes the blob
    def _cold_dict(self) -> Dict[str, Any]:
        blob = self._cold
        return _decode(blob) if blob else {}

    def __missing__(self, key):
        if self._cold:
            cold = self._cold_dict()
            if key in cold:
                return cold[key]
        raise KeyError(key)

    def get(self, key, default=None):
        v = _dict_get(self, key, _MISSING)
        if v is not _MISSING:
            return v
        if self._cold and (key in COLD_FIELDS or key == CALLDATA_KEY):
            return self._cold_dict().get(key, default)
        return default

    def __contains__(self, key) -> bool:
        if _dict_contains(self, key):
            return True
        return bool(self._cold) and (key in COLD_FIELDS or key == CALLDATA_KEY) and key in self._cold_dict()

    def hot_get(self, key, default=None):
        """Like get() but never decodes the blob: a cold field reads as `default`."""
        return _dict_get(self, key, default)

    def has_cold(self) -> bool:
        return bool(self._cold)

    # ---- the whole record
    def full(self) -> Dict[str, Any]:
        """A plain dict with every field, in the original key order."""
        if not self._cold:
            return dict(dict.items(self))
        cold = self._cold_dict()
        out: Dict[str, Any] = {}
        for k in self._order:
            if _dict_contains(self, k):
                out[k] = _dict_getitem(self, k)
            elif k in cold:
                out[k] = cold[k]
        for k, v in dict.items(self):      # keys added after the record was built
            if k not in out:
                out[k] = v
        return out

    def hot(self) -> Dict[str, Any]:
        """A plain dict with the hot fields only (cheap)."""
        return dict(dict.items(self))

    def copy(self) -> Dict[str, Any]:
        return self.full()

    def items(self):
        return self.full().items()

    def keys(self):
        return self.full().keys()

    def values(self):
        return self.full().values()

    def __iter__(self) -> Iterator[str]:
        return iter(self.full())

    def __bool__(self) -> bool:      # `if not d` must not decode the blob (it would call __len__)
        return dict.__len__(self) > 0 or bool(self._cold)

    def __len__(self) -> int:
        return len(self.full()) if self._cold else dict.__len__(self)

    def __eq__(self, other) -> bool:
        if isinstance(other, CompactRecord):
            return self.full() == other.full()
        return self.full() == other

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)

    __hash__ = None  # type: ignore

    def __repr__(self) -> str:
        return repr(self.full())

    def __reduce__(self):
        return (dict, (self.full(),))


def full_record(p):
    """`p` as a plain dict with every field (a copy for a CompactRecord, `p` itself otherwise: callers that
    need their own copy still make it)."""
    return p.full() if isinstance(p, CompactRecord) else p


def compact_record(rec: Dict[str, Any], level: int = 1) -> Dict[str, Any]:
    """`rec` as a CompactRecord (a new object; `rec` is not changed). A record that is already compact, not a dict, or
    has nothing worth compressing is returned as it is."""
    if isinstance(rec, CompactRecord) or not isinstance(rec, dict):
        return rec
    cold: Dict[str, Any] = {}
    hot: Dict[str, Any] = {}
    for k, v in rec.items():
        if k in COLD_FIELDS:
            cold[k] = v
        elif k == CALLDATA_KEY and isinstance(v, str) and len(v) > CALLDATA_COLD_MIN_CHARS:
            cold[k] = v
        else:
            if k in INTERN_KEYS and isinstance(v, str) and len(v) <= 64:
                v = sys.intern(v)
            hot[sys.intern(k) if isinstance(k, str) else k] = v
    # smart_minters usually repeats smart_wallets exactly: keep one list
    sw, sm = hot.get("smart_wallets"), hot.get("smart_minters")
    if isinstance(sw, list) and isinstance(sm, list) and sw is not sm and sw == sm:
        hot["smart_minters"] = sw
    out = CompactRecord(hot)
    order = tuple(rec)
    out._order = _ORDER_POOL.setdefault(order, order)
    out._cold = (zlib.compress(json.dumps(cold, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), level)
                 if cold else _NO_COLD)
    return out
