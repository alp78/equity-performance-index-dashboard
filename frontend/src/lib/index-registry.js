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


// ── Leader stocks per index ─────────────────────────────────────────────

export const SYMBOL_SETS = Object.fromEntries(
    Object.entries(_indices).map(([key, cfg]) => {
        const displayMap = cfg.leaderDisplayMap || {};
        return [key, {
            title: 'MARKET LEADERS',
            subtitle: cfg.label,
            symbols: (cfg.leaders || []).map(s => displayMap[s] || s),
        }];
    })
);


// ── Symbol → market-code map (leader stocks + special instruments) ──────

export const SYMBOL_MARKET_MAP = {};
for (const cfg of Object.values(_indices)) {
    const ex = cfg.exchange;
    if (!ex?.marketCode) continue;
    const displayMap = cfg.leaderDisplayMap || {};
    for (const sym of (cfg.leaders || [])) {
        const display = displayMap[sym] || sym;
        SYMBOL_MARKET_MAP[display] = ex.marketCode;
    }
}
// Non-index instruments
SYMBOL_MARKET_MAP['BINANCE:BTCUSDT'] = 'CRYPTO';
SYMBOL_MARKET_MAP['FXCM:EUR/USD'] = 'FOREX';
SYMBOL_MARKET_MAP['FXCM:XAU/USD'] = 'COMMOD';


// ── Display → raw symbol reverse map (for sidebar navigation) ───────────

export const LEADER_TO_SIDEBAR = {};
for (const cfg of Object.values(_indices)) {
    const displayMap = cfg.leaderDisplayMap || {};
    for (const [raw, display] of Object.entries(displayMap)) {
        if (raw !== display) {
            LEADER_TO_SIDEBAR[display] = raw;
        }
    }
}


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
