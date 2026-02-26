# =============================================================================
#  Index Configuration Loader
# =============================================================================
#  Reads config/indices.json (the single source of truth for all index metadata)
#  and generates every derived lookup dict used by main.py.
#
#  Adding a new index requires only adding an entry to indices.json — every dict
#  below is regenerated automatically.
# =============================================================================

import json
from os import getenv
from pathlib import Path

# Dev: ../config/indices.json (relative to backend/)
# Docker: /config/indices.json (COPY config/ /config/)
_DEV_PATH = Path(__file__).parent.parent / "config" / "indices.json"
_DOCKER_PATH = Path("/config/indices.json")
_CONFIG_PATH = Path(getenv("INDEX_CONFIG_PATH", "")) if getenv("INDEX_CONFIG_PATH") \
    else (_DEV_PATH if _DEV_PATH.exists() else _DOCKER_PATH)


def _load() -> dict:
    with open(_CONFIG_PATH) as f:
        return json.load(f)


_RAW = _load()
_DEFAULTS = _RAW.get("_defaults", {})
_INDICES = _RAW.get("indices", {})

PROJECT_ID = getenv("PROJECT_ID")
DATASET_ID = getenv("DATASET_ID", _DEFAULTS.get("bqDataset", "stock_exchange"))


# ── Core index registry (table_id computed from env + config) ──────────────

MARKET_INDICES = {
    key: {
        "table_id": f"{PROJECT_ID}.{DATASET_ID}.{cfg['bqTable']}" if PROJECT_ID else None,
    }
    for key, cfg in _INDICES.items()
}


# ── Ticker ↔ key bidirectional maps ───────────────────────────────────────

INDEX_KEY_TO_TICKER = {key: cfg["ticker"] for key, cfg in _INDICES.items()}
INDEX_TICKER_TO_KEY = {v: k for k, v in INDEX_KEY_TO_TICKER.items()}

# ── Ticker → currency code (for FX adjustment) ──────────────────────────
INDEX_TICKER_TO_CURRENCY = {cfg["ticker"]: cfg["currencyCode"] for key, cfg in _INDICES.items()}


# ── Ticker suffix → index key (for lazy-loading by symbol) ───────────────

SUFFIX_TO_INDEX = {}
for key, cfg in _INDICES.items():
    for suffix in cfg.get("tickerSuffixes", []):
        SUFFIX_TO_INDEX[suffix] = key


# ── News configuration ───────────────────────────────────────────────────

NEWS_INDEX_TICKERS = {
    key: cfg.get("newsTickers", []) for key, cfg in _INDICES.items()
}

INDEX_FLAG_MAP = {
    key: cfg.get("flagCode", "") for key, cfg in _INDICES.items()
}

KEYWORD_INDEX_MAP = [
    (cfg.get("newsKeywords", []), key)
    for key, cfg in _INDICES.items()
    if cfg.get("newsKeywords")
]


# ── Startup phasing ─────────────────────────────────────────────────────

_default_priority = _DEFAULTS.get("priority", 2)
PHASE1_INDICES = [
    key for key, cfg in _INDICES.items()
    if cfg.get("priority", _default_priority) == 1
]


# ── Correlation matrix ordering ──────────────────────────────────────────

CORRELATION_ORDER = list(_INDICES.keys())


# ── Leader stocks for real-time market data feeder ───────────────────────

ALL_LEADER_SYMBOLS = []
LEADER_DISPLAY_MAP = {}
LEADER_SYMBOL_SETS = {}

for key, cfg in _INDICES.items():
    leaders = cfg.get("leaders", [])
    display_map = cfg.get("leaderDisplayMap", {})
    ALL_LEADER_SYMBOLS.extend(leaders)
    LEADER_DISPLAY_MAP.update(display_map)
    LEADER_SYMBOL_SETS[key] = {
        "title": "MARKET LEADERS",
        "subtitle": cfg.get("label", key),
        "symbols": [display_map.get(s, s) for s in leaders],
    }


# ── Exchange metadata (for frontend components served via /api/config) ───

INDEX_EXCHANGE_INFO = {
    key: cfg.get("exchange", {}) for key, cfg in _INDICES.items()
}


# ── Full index config (subset safe to expose to frontend) ───────────────

INDEX_CONFIG_PUBLIC = {
    key: {
        "label": cfg["label"],
        "shortLabel": cfg["shortLabel"],
        "abbr": cfg["abbr"],
        "ticker": cfg["ticker"],
        "flag": cfg["flag"],
        "flagCode": cfg.get("flagCode", ""),
        "currency": cfg["currency"],
        "currencyCode": cfg["currencyCode"],
        "region": cfg["region"],
        "color": cfg["color"],
        "defaultSymbol": cfg["defaultSymbol"],
        "exchange": cfg.get("exchange", {}),
        "leaders": cfg.get("leaders", []),
        "leaderDisplayMap": cfg.get("leaderDisplayMap", {}),
    }
    for key, cfg in _INDICES.items()
}
