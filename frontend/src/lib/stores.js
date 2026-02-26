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
export const crossIndexHighlight = writable(null); // index key to highlight in cross-index chart

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


// Data stores & loaders: extracted to $lib/data-loaders.js
export { summaryData, rankingsData, indexOverviewData, loadSummaryData, loadIndexOverviewData, loadRankingsData } from '$lib/data-loaders.js';


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


