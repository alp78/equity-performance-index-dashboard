/**
 * =========================================================================
 *  Index Registry — Single-Import Frontend Config
 * =========================================================================
 *  Reads config/indices.json (via Vite $config alias) and derives every
 *  lookup map that components need.  All other files import from here
 *  (or via re-exports in stores.js / theme.js for backward compat).
 *
 *  Adding a new index = adding an entry in config/indices.json.
 * =========================================================================
 */

import rawConfig from '$config/indices.json';

const _indices = rawConfig.indices;

// ── Core index config (frontend-safe subset) ────────────────────────────

export const INDEX_CONFIG = Object.fromEntries(
    Object.entries(_indices).map(([key, cfg]) => [key, {
        label:         cfg.label,
        shortLabel:    cfg.shortLabel,
        abbr:          cfg.abbr,
        flag:          cfg.flag,
        currency:      cfg.currency,
        currencyCode:  cfg.currencyCode,
        defaultSymbol: cfg.defaultSymbol,
        region:        cfg.region,
    }])
);


// ── Ticker ↔ key bidirectional maps ─────────────────────────────────────

export const INDEX_TICKER_MAP = Object.fromEntries(
    Object.entries(_indices).map(([key, cfg]) => [cfg.ticker, key])
);

export const INDEX_KEY_TO_TICKER = Object.fromEntries(
    Object.entries(_indices).map(([key, cfg]) => [key, cfg.ticker])
);


// ── Per-index accent colors ─────────────────────────────────────────────

export const INDEX_COLORS = Object.fromEntries(
    Object.entries(_indices).map(([key, cfg]) => [key, cfg.color])
);


// ── Sector-level display names ──────────────────────────────────────────

export const SECTOR_INDEX_NAMES = Object.fromEntries(
    Object.entries(_indices).map(([key, cfg]) => [key, cfg.shortLabel])
);


// ── Exchange info per index ─────────────────────────────────────────────

export const INDEX_EXCHANGE_INFO = Object.fromEntries(
    Object.entries(_indices).map(([key, cfg]) => [key, cfg.exchange || {}])
);


// ── Market hours (keyed by marketCode) ──────────────────────────────────

export const MARKET_HOURS = {};
for (const cfg of Object.values(_indices)) {
    const ex = cfg.exchange;
    if (!ex || !ex.marketCode) continue;
    if (MARKET_HOURS[ex.marketCode]) continue;
    const [oh, om] = (ex.hours?.open || '09:00').split(':').map(Number);
    const [ch, cm] = (ex.hours?.close || '17:00').split(':').map(Number);
    MARKET_HOURS[ex.marketCode] = {
        tz: ex.tz,
        open: { h: oh, m: om },
        close: { h: ch, m: cm },
        weekdays: true,
    };
}
// Non-index markets (always present)
MARKET_HOURS['CRYPTO'] = { tz: null };
MARKET_HOURS['FOREX']  = { tz: 'America/New_York', open: { h: 17, m: 0 }, close: { h: 17, m: 0 }, weekdays: true, sundayOpen: true };
MARKET_HOURS['COMMOD'] = { tz: 'America/New_York', open: { h: 18, m: 0 }, close: { h: 17, m: 0 }, weekdays: true, sundayOpen: true };


// ── Symbol → market-code map (special macro instruments) ─────────────────

export const SYMBOL_MARKET_MAP = {
    'BINANCE:BTCUSDT': 'CRYPTO',
    'FXCM:EUR/USD':    'FOREX',
    'FXCM:XAU/USD':    'COMMOD',
};


// ── Index metadata keyed by ticker (for IndexPerformanceTable) ──────────

export const INDEX_META_BY_TICKER = Object.fromEntries(
    Object.entries(_indices).map(([key, cfg]) => [cfg.ticker, {
        name:   cfg.shortLabel,
        short:  cfg.abbr,
        flag:   cfg.flag,
        color:  cfg.color,
        ccy:    cfg.currency,
        market: cfg.exchange?.marketCode || '',
        key,
    }])
);


// ── FX pair metadata (derived from exchange.fxPair) ─────────────────────

// All index-derived FX pairs (ordered by config order, excludes USD)
export const INDEX_FX_PAIRS = Object.values(_indices)
    .map(cfg => cfg.exchange?.fxPair)
    .filter(Boolean);

// FX flag map: pair → flag CSS class (e.g. "EUR/USD" → "fi-eu")
export const FX_FLAG_MAP = Object.fromEntries([
    ...Object.values(_indices)
        .filter(cfg => cfg.exchange?.fxPair)
        .map(cfg => [cfg.exchange.fxPair, `fi-${cfg.flagCode}`]),
    ...(rawConfig._extraFxPairs || [])
        .map(p => [p.pair, `fi-${p.flagCode}`]),
]);

// Zero-decimal currencies (ISO 4217) get 2 decimal places, others get 4
const _ZERO_DECIMAL_CCYS = new Set(['JPY', 'KRW', 'VND', 'CLP', 'ISK', 'HUF']);

export function fxDecimals(pair) {
    const ccy = pair.replace('USD/', '').replace('/USD', '');
    return _ZERO_DECIMAL_CCYS.has(ccy) ? 2 : 4;
}

export function fmtFx(pair, val) {
    if (val == null) return '\u2014';
    return val.toFixed(fxDecimals(pair));
}
