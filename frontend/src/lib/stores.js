/**
 * =========================================================================
 *  Global State Management & Data Loaders
 * =========================================================================
 *  Central nervous system of the dashboard. This file owns:
 *
 *  1. INDEX_CONFIG  — re-exported from index-registry.js (driven by
 *     config/indices.json, the single source of truth).
 *  2. Svelte stores  — reactive state for the currently selected index,
 *     stock, sector, and analysis mode. All stores are persisted to
 *     sessionStorage so a page refresh doesn't lose the user's context.
 *  3. Data loaders   — async functions that fetch stock summaries, rankings,
 *     and index-overview data from the FastAPI backend, with retry logic
 *     and a two-tier (memory + sessionStorage) stale-while-revalidate
 *     cache.
 *
 *  Every component and page in the app imports from this file.
 * =========================================================================
 */

import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';
import { API_BASE_URL } from '$lib/config.js';

// Re-export from centralized registry (config/indices.json)
export { INDEX_CONFIG, INDEX_TICKER_MAP, INDEX_KEY_TO_TICKER } from '$lib/index-registry.js';
import { INDEX_CONFIG, INDEX_TICKER_MAP } from '$lib/index-registry.js';


// ── Region Groups (for sidebar dropdown) ─────────────────────────────────
// Splits indices into West (Americas + Europe) and East (Asia) so the
// dropdown renders two visually distinct groups.

export const INDEX_GROUPS = [
    {
        label: 'West',
        indices: Object.entries(INDEX_CONFIG)
            .filter(([, v]) => v.region === 'west')
            .map(([key, v]) => ({ key, ...v })),
    },
    {
        label: 'East',
        indices: Object.entries(INDEX_CONFIG)
            .filter(([, v]) => v.region === 'east')
            .map(([key, v]) => ({ key, ...v })),
    },
];


/* ═══════════════════════════════════════════════════════════════════════════
 *  2. SESSION-PERSISTED STORES
 *  Every user choice (selected index, stock, sector, analysis mode) is
 *  saved to sessionStorage so a page refresh doesn't lose context.
 *  Session data is cleared when the browser tab closes — this is
 *  intentional so users start fresh each visit.
 * ═══════════════════════════════════════════════════════════════════════ */

// ── Restore previous session values (or fall back to defaults) ───────────

const _storedIndex = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_index');
        return (v && (INDEX_CONFIG[v] || v === 'macro' || v === 'sectors' || v === 'context')) ? v : 'macro';
    } catch { return 'macro'; }
})() : 'macro';

const _storedSymbol = browser ? (() => {
    try {
        return sessionStorage.getItem('dash_symbol') || INDEX_CONFIG[_storedIndex]?.defaultSymbol || 'ASML.AS';
    } catch { return 'ASML.AS'; }
})() : 'ASML.AS';

export const selectedSymbol = writable(_storedSymbol);
export const marketIndex = writable(_storedIndex);

// Overview mode: which index tickers the user is comparing on the
// multi-index line chart (defaults to all six).
const ALL_INDEX_TICKERS = Object.keys(INDEX_TICKER_MAP);

const _storedOverviewIndices = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_overview_indices');
        return v ? JSON.parse(v) : ALL_INDEX_TICKERS;
    } catch { return ALL_INDEX_TICKERS; }
})() : ALL_INDEX_TICKERS;

export const overviewSelectedIndices = writable(_storedOverviewIndices);

// Sector mode: cross-index compares all indices side-by-side,
// single-index drills into one market's sector/industry breakdown.
const ALL_INDEX_KEYS = Object.keys(INDEX_CONFIG);

const _storedSectorIndices = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_sector_indices');
        return v ? JSON.parse(v) : ALL_INDEX_KEYS;
    } catch { return ALL_INDEX_KEYS; }
})() : ALL_INDEX_KEYS;

const _storedSingleIndex = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_single_index');
        return v ? JSON.parse(v) : ['stoxx50'];
    } catch { return ['stoxx50']; }
})() : ['stoxx50'];

const _storedSelectedSector = browser ? (() => {
    try {
        return sessionStorage.getItem('dash_selected_sector') || 'Materials';
    } catch { return 'Materials'; }
})() : 'Materials';

export const sectorSelectedIndices = writable(_storedSectorIndices);
export const singleSelectedIndex   = writable(_storedSingleIndex);
export const selectedSector = writable(_storedSelectedSector);
export const sectorHighlightEnabled = writable(true);

const _storedSectorMode = browser ? (() => {
    try { return sessionStorage.getItem('dash_sector_mode') || 'cross-index'; } catch { return 'cross-index'; }
})() : 'cross-index';

const _storedSectorIndustries = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_sector_industries');
        return v ? JSON.parse(v) : [];
    } catch { return []; }
})() : [];

// The 11 GICS sectors shared across all six indices.
export const ALL_SECTORS = ['Communication Services', 'Consumer Discretionary', 'Consumer Staples', 'Energy', 'Financials', 'Healthcare', 'Industrials', 'Information Technology', 'Materials', 'Real Estate', 'Utilities'];

// Per-index sector checkbox state: { sp500: [...checked], stoxx50: [...checked] }
const _storedSectorsByIndex = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_sectors_by_index');
        return v ? JSON.parse(v) : {};
    } catch { return {}; }
})() : {};

const _currentSingleIndex = _storedSectorIndices[0] || 'stoxx50';
const _initialSectors = _storedSectorsByIndex[_currentSingleIndex] || ALL_SECTORS;

// Per-sector industry selection for cross-index mode: { sectorName: [industryNames] }
// Empty array = all industries selected (convention shared with single-index mode)
const _storedCrossIndustries = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_cross_industries');
        return v ? JSON.parse(v) : {};
    } catch { return {}; }
})() : {};

// Per-sector industry selection for single-index mode.
// Empty array or absent key = all industries selected.
const _storedSingleIndustries = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_single_industries');
        return v ? JSON.parse(v) : {};
    } catch { return {}; }
})() : {};

export const sectorAnalysisMode = writable(_storedSectorMode);
export const selectedIndustries = writable(_storedSectorIndustries);
export const crossSelectedIndustries = writable(_storedCrossIndustries);
export const singleModeIndustries = writable(_storedSingleIndustries);
export const selectedSectors = writable(_initialSectors);
export const sectorsByIndex = writable(_storedSectorsByIndex);


// ── Derived Stores ───────────────────────────────────────────────────────
// Computed values that react automatically when their source stores change.

export const isOverviewMode = derived(marketIndex, ($idx) => $idx === 'macro');
export const isSectorMode = derived(marketIndex, ($idx) => $idx === 'sectors');
export const isMacroContextMode = derived(marketIndex, ($idx) => $idx === 'context');
export const currentCurrency = derived(marketIndex, ($idx) =>
    ($idx === 'macro' || $idx === 'sectors') ? '%' : (INDEX_CONFIG[$idx]?.currency || '$')
);


// ── Debounced Session Persistence ────────────────────────────────────────
// Instead of writing to sessionStorage on every keystroke, we batch all
// store changes and flush them in a single write every 300 ms.

const _pendingWrites = {};
let _persistTimer;

function _schedulePersist(key, val) {
    _pendingWrites[key] = val;
    if (!_persistTimer) {
        _persistTimer = setTimeout(() => {
            _persistTimer = null;
            try {
                for (const [k, v] of Object.entries(_pendingWrites)) {
                    sessionStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v));
                }
            } catch {}
            for (const k in _pendingWrites) delete _pendingWrites[k];
        }, 300);
    }
}

if (browser) {
    marketIndex.subscribe(v => _schedulePersist('dash_index', v));
    selectedSymbol.subscribe(v => _schedulePersist('dash_symbol', v));
    overviewSelectedIndices.subscribe(v => _schedulePersist('dash_overview_indices', v));
    sectorSelectedIndices.subscribe(v => _schedulePersist('dash_sector_indices', v));
    singleSelectedIndex.subscribe(v => _schedulePersist('dash_single_index', v));
    selectedSector.subscribe(v => _schedulePersist('dash_selected_sector', v));
    sectorAnalysisMode.subscribe(v => _schedulePersist('dash_sector_mode', v));
    selectedIndustries.subscribe(v => _schedulePersist('dash_sector_industries', v));
    crossSelectedIndustries.subscribe(v => _schedulePersist('dash_cross_industries', v));
    singleModeIndustries.subscribe(v => _schedulePersist('dash_single_industries', v));
    selectedSectors.subscribe(v => _schedulePersist('dash_selected_sectors', v));
    sectorsByIndex.subscribe(v => _schedulePersist('dash_sectors_by_index', v));
}


/* ═══════════════════════════════════════════════════════════════════════════
 *  3. DATA STORES & API LOADERS
 *  Reactive stores that hold the data returned by the backend API.
 *  Each loader uses a stale-while-revalidate (SWR) strategy: serve
 *  cached data instantly to the UI, then refresh in the background.
 *  Retries with exponential backoff handle the case where the backend
 *  is still loading index data from BigQuery on cold start.
 * ═══════════════════════════════════════════════════════════════════════ */

export const summaryData = writable({
    assets: [],
    loaded: false,
    loading: false,
    error: null,
});

export const rankingsData = writable({
    rankings: { selected: { top: [], bottom: [] } },
    period: '1y',
    loaded: false,
    loading: false,
    error: null,
});

export const indexOverviewData = writable({
    assets: [],
    loaded: false,
    loading: false,
});


// ── Fetch Helper ─────────────────────────────────────────────────────────
// Generic JSON fetcher with automatic retry and per-request timeout.

async function fetchWithRetry(url, retries = 2, timeout = 10000) {
    for (let i = 0; i < retries; i++) {
        try {
            const controller = new AbortController();
            const id = setTimeout(() => controller.abort(), timeout);
            const response = await fetch(url, { signal: controller.signal });
            clearTimeout(id);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            if (i === retries - 1) throw error;
            await new Promise(r => setTimeout(r, 1000));
        }
    }
}


// ── Stock Summary Loader ─────────────────────────────────────────────────
// Fetches the full stock list (symbol, name, price, volume, sector, etc.)
// for one index. Retries up to 5 times because the backend may still be
// loading that index from BigQuery after a cold start.

const SUMMARY_TTL = 2 * 60 * 1000; // 2 min — stock list refreshes frequently

export async function loadSummaryData(index = 'stoxx50') {
    if (!browser) return;
    const cacheKey = `summary_${index}`;
    // SWR: serve cached instantly, revalidate in background if stale
    const cached = getCached(cacheKey);
    if (cached) {
        summaryData.set({ assets: cached.data, loaded: true, loading: false, error: null });
        if (isCacheFresh(cacheKey)) return cached.data;
    } else {
        summaryData.update(s => ({ ...s, loading: true, error: null }));
    }

    const maxRetries = 5;
    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const data = await fetchWithRetry(
                `${API_BASE_URL}/summary?index=${index}&t=${Date.now()}`
            );
            if (data && data.length > 0) {
                summaryData.set({ assets: data, loaded: true, loading: false, error: null });
                setCached(cacheKey, data, SUMMARY_TTL);
                return data;
            }
            if (attempt < maxRetries - 1) {
                await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
                continue;
            }
        } catch (error) {
            if (attempt < maxRetries - 1) {
                await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
                continue;
            }
            if (!cached) summaryData.set({ assets: [], loaded: false, loading: false, error: error.message });
            return;
        }
    }
    if (!cached) summaryData.set({ assets: [], loaded: true, loading: false, error: null });
}


// ── Index Overview Loader ────────────────────────────────────────────────
// Fetches aggregated stats for ALL six indices at once (return, vol,
// 52-week range) to populate the macro overview comparison table.

const OVERVIEW_TTL = 5 * 60 * 1000;

export async function loadIndexOverviewData() {
    if (!browser) return;
    const cached = getCached('index_overview');
    if (cached) {
        indexOverviewData.set({ assets: cached.data, loaded: true, loading: false });
        if (isCacheFresh('index_overview')) return cached.data;
    } else {
        indexOverviewData.update(s => ({ ...s, loading: true }));
    }
    try {
        const data = await fetchWithRetry(
            `${API_BASE_URL}/index-prices/summary`
        );
        indexOverviewData.set({ assets: data || [], loaded: true, loading: false });
        setCached('index_overview', data || [], OVERVIEW_TTL);
        return data;
    } catch (error) {
        if (!cached) indexOverviewData.set({ assets: [], loaded: false, loading: false });
    }
}


// ── Rankings Loader ──────────────────────────────────────────────────────
// Fetches the top/bottom stock performers for one index over a given time
// period (1w, 1mo, 1y, etc.), used by the "Top Movers" panel.

const RANKINGS_TTL = 5 * 60 * 1000; // 5 min — rankings change slowly

export async function loadRankingsData(period = '1y', index = 'stoxx50') {
    if (!browser) return;
    const cacheKey = `rankings_${period}_${index}`;
    const cached = getCached(cacheKey);
    if (cached) {
        rankingsData.set({ rankings: cached.data, period, loaded: true, loading: false, error: null });
        if (isCacheFresh(cacheKey)) return cached.data;
    } else {
        rankingsData.update(s => ({ ...s, loading: true, error: null }));
    }
    try {
        const data = await fetchWithRetry(
            `${API_BASE_URL}/rankings?period=${period}&index=${index}&t=${Date.now()}`
        );
        rankingsData.set({ rankings: data, period, loaded: true, loading: false, error: null });
        setCached(cacheKey, data, RANKINGS_TTL);
        return data;
    } catch (error) {
        if (!cached) {
            rankingsData.set({
                rankings: { selected: { top: [], bottom: [] } },
                period, loaded: false, loading: false, error: error.message,
            });
        }
    }
}


/* ═══════════════════════════════════════════════════════════════════════════
 *  4. UI INTERACTION STORES
 *  Lightweight stores that coordinate behavior between distant components
 *  (e.g., clicking a stock in "Top Movers" scrolls the Sidebar to it).
 * ═══════════════════════════════════════════════════════════════════════ */

// Triggers sidebar scroll+expand even when the same symbol is re-clicked.
// The incrementing `seq` number forces reactivity on repeated values.
export const focusSymbolRequest = writable({ symbol: '', seq: 0 });
export function requestFocusSymbol(symbol) {
    focusSymbolRequest.update(v => ({ symbol, seq: v.seq + 1 }));
}

// When hovering a pill in the Risk Dashboard, highlight the matching
// rows in the Macro Watchlist. Values: null | 'vix' | 'curve' | 'credit' | 'usd' | etc.
export const riskHighlight = writable(null);

// When clicking an index row in MacroPanel sidebar, highlight that index
// across chart, table, and correlation heatmap. Values: null | 'sp500' | 'stoxx50' | etc.
export const macroHighlightIndex = writable(null);

// When clicking a correlation heatmap cell, highlight the pair of indices.
// Values: null | { row: 'sp500', col: 'nikkei225' }
export const macroHighlightPair = writable(null);


/* ═══════════════════════════════════════════════════════════════════════════
 *  5. TWO-TIER DATA CACHE
 *  Provides fast, SWR-style caching for API responses:
 *
 *  Tier 1 — In-memory LRU Map (instant, survives mode switches).
 *  Tier 2 — sessionStorage (slower, survives full page refresh).
 *
 *  When data is read, Tier 2 entries are promoted to Tier 1.
 *  The LRU cap (200 entries) prevents unbounded memory growth.
 * ═══════════════════════════════════════════════════════════════════════ */

const _memCache = new Map();
const _MEM_CACHE_MAX = 200;

function _evictOldest() {
    if (_memCache.size <= _MEM_CACHE_MAX) return;
    const oldest = _memCache.keys().next().value;
    _memCache.delete(oldest);
}

export function getCached(key) {
    // Tier 1: in-memory (instant, LRU promotion)
    if (_memCache.has(key)) {
        const entry = _memCache.get(key);
        _memCache.delete(key);
        _memCache.set(key, entry);
        return entry;
    }
    // Tier 2: sessionStorage (survives refresh)
    if (browser) {
        try {
            const raw = sessionStorage.getItem('cache_' + key);
            if (raw) {
                const entry = JSON.parse(raw);
                _memCache.set(key, entry);
                _evictOldest();
                return entry;
            }
        } catch {}
    }
    return null;
}

export function setCached(key, data, ttlMs) {
    const entry = { data, ts: Date.now(), ttl: ttlMs };
    _memCache.delete(key);
    _memCache.set(key, entry);
    _evictOldest();
    if (browser) {
        try { sessionStorage.setItem('cache_' + key, JSON.stringify(entry)); } catch {}
    }
}

export function isCacheFresh(key) {
    const entry = getCached(key);
    if (!entry) return false;
    return (Date.now() - entry.ts) < entry.ttl;
}
