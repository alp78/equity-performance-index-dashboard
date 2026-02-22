// Global stores and data loaders — INDEX_CONFIG is the single source of truth for all indices

import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';
import { API_BASE_URL } from '$lib/config.js';

// --- INDEX CONFIGURATION ---

export const INDEX_CONFIG = {
    stoxx50: {
        label: 'EURO STOXX 50',
        shortLabel: 'STOXX 50',
        currency: '€',
        currencyCode: 'EUR',
        defaultSymbol: 'ASML.AS',
        region: 'west',
    },
    sp500: {
        label: 'S&P 500',
        shortLabel: 'S&P 500',
        currency: '$',
        currencyCode: 'USD',
        defaultSymbol: 'NVDA',
        region: 'west',
    },
    ftse100: {
        label: 'FTSE 100',
        shortLabel: 'FTSE 100',
        currency: '£',
        currencyCode: 'GBP',
        defaultSymbol: 'SHEL.L',
        region: 'west',
    },
    nikkei225: {
        label: 'Nikkei 225',
        shortLabel: 'Nikkei 225',
        currency: '¥',
        currencyCode: 'JPY',
        defaultSymbol: '7203.T',
        region: 'east',
    },
    csi300: {
        label: 'CSI 300',
        shortLabel: 'CSI 300',
        currency: '¥',
        currencyCode: 'CNY',
        defaultSymbol: '600519.SS',
        region: 'east',
    },
    nifty50: {
        label: 'NIFTY 50',
        shortLabel: 'NIFTY 50',
        currency: '₹',
        currencyCode: 'INR',
        defaultSymbol: 'RELIANCE.NS',
        region: 'east',
    },
};

// ticker symbol <-> index key bidirectional mapping
export const INDEX_TICKER_MAP = {
    '^GSPC':      'sp500',
    '^STOXX50E':  'stoxx50',
    '^FTSE':      'ftse100',
    '^N225':      'nikkei225',
    '000300.SS':  'csi300',
    '^NSEI':      'nifty50',
};

export const INDEX_KEY_TO_TICKER = Object.fromEntries(
    Object.entries(INDEX_TICKER_MAP).map(([ticker, key]) => [key, ticker])
);

// grouped by region for dropdown rendering
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

// --- PERSISTED STORES ---
// all stores restore from sessionStorage on page load (clears when browser is closed)

const _storedIndex = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_index');
        return (v && (INDEX_CONFIG[v] || v === 'overview' || v === 'sectors')) ? v : 'stoxx50';
    } catch { return 'stoxx50'; }
})() : 'stoxx50';

const _storedSymbol = browser ? (() => {
    try {
        return sessionStorage.getItem('dash_symbol') || INDEX_CONFIG[_storedIndex]?.defaultSymbol || 'ASML.AS';
    } catch { return 'ASML.AS'; }
})() : 'ASML.AS';

export const selectedSymbol = writable(_storedSymbol);
export const marketIndex = writable(_storedIndex);

// overview mode: selected index tickers for comparison chart
const _storedOverviewIndices = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_overview_indices');
        return v ? JSON.parse(v) : ['^GSPC', '^STOXX50E'];
    } catch { return ['^GSPC', '^STOXX50E']; }
})() : ['^GSPC', '^STOXX50E'];

export const overviewSelectedIndices = writable(_storedOverviewIndices);

// sector mode: cross-index uses multiple indices, single-index uses one
const _storedSectorIndices = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_sector_indices');
        return v ? JSON.parse(v) : ['sp500', 'stoxx50'];
    } catch { return ['sp500', 'stoxx50']; }
})() : ['sp500', 'stoxx50'];

const _storedSingleIndex = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_single_index');
        return v ? JSON.parse(v) : ['stoxx50'];
    } catch { return ['stoxx50']; }
})() : ['stoxx50'];

const _storedSelectedSector = browser ? (() => {
    try {
        return sessionStorage.getItem('dash_selected_sector') || 'Technology';
    } catch { return 'Technology'; }
})() : 'Technology';

export const sectorSelectedIndices = writable(_storedSectorIndices);
export const singleSelectedIndex   = writable(_storedSingleIndex);
export const selectedSector = writable(_storedSelectedSector);

const _storedSectorMode = browser ? (() => {
    try { return sessionStorage.getItem('dash_sector_mode') || 'cross-index'; } catch { return 'cross-index'; }
})() : 'cross-index';

const _storedSectorIndustries = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_sector_industries');
        return v ? JSON.parse(v) : [];
    } catch { return []; }
})() : [];

export const ALL_SECTORS = ['Technology', 'Financial Services', 'Healthcare', 'Industrials', 'Consumer Cyclical', 'Communication Services', 'Consumer Defensive', 'Energy', 'Basic Materials', 'Utilities', 'Real Estate'];

// per-index sector checkbox state: { sp500: [...], stoxx50: [...] }
const _storedSectorsByIndex = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_sectors_by_index');
        return v ? JSON.parse(v) : {};
    } catch { return {}; }
})() : {};

const _currentSingleIndex = _storedSectorIndices[0] || 'stoxx50';
const _initialSectors = _storedSectorsByIndex[_currentSingleIndex] || ALL_SECTORS;

// per-sector industry selection for cross-index mode: { sectorName: [industryNames] }
// empty array = all selected (same convention as singleSelectedIndustries)
const _storedCrossIndustries = browser ? (() => {
    try {
        const v = sessionStorage.getItem('dash_cross_industries');
        return v ? JSON.parse(v) : {};
    } catch { return {}; }
})() : {};

// per-sector industry selection for single-index mode: { sectorName: [industryNames] }
// empty array or absent = all selected
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

// --- DERIVED STORES ---

export const isOverviewMode = derived(marketIndex, ($idx) => $idx === 'overview');
export const isSectorMode = derived(marketIndex, ($idx) => $idx === 'sectors');
export const currentCurrency = derived(marketIndex, ($idx) =>
    ($idx === 'overview' || $idx === 'sectors') ? '%' : (INDEX_CONFIG[$idx]?.currency || '$')
);

// --- PERSISTENCE ---
// subscribe to each store and sync to sessionStorage on change

if (browser) {
    marketIndex.subscribe(val => {
        try { sessionStorage.setItem('dash_index', val); } catch {}
    });
    selectedSymbol.subscribe(val => {
        try { sessionStorage.setItem('dash_symbol', val); } catch {}
    });
    overviewSelectedIndices.subscribe(val => {
        try { sessionStorage.setItem('dash_overview_indices', JSON.stringify(val)); } catch {}
    });
    sectorSelectedIndices.subscribe(val => {
        try { sessionStorage.setItem('dash_sector_indices', JSON.stringify(val)); } catch {}
    });
    singleSelectedIndex.subscribe(val => {
        try { sessionStorage.setItem('dash_single_index', JSON.stringify(val)); } catch {}
    });
    selectedSector.subscribe(val => {
        try { sessionStorage.setItem('dash_selected_sector', val); } catch {}
    });
    sectorAnalysisMode.subscribe(val => {
        try { sessionStorage.setItem('dash_sector_mode', val); } catch {}
    });
    selectedIndustries.subscribe(val => {
        try { sessionStorage.setItem('dash_sector_industries', JSON.stringify(val)); } catch {}
    });
    crossSelectedIndustries.subscribe(val => {
        try { sessionStorage.setItem('dash_cross_industries', JSON.stringify(val)); } catch {}
    });
    singleModeIndustries.subscribe(val => {
        try { sessionStorage.setItem('dash_single_industries', JSON.stringify(val)); } catch {}
    });
    selectedSectors.subscribe(val => {
        try { sessionStorage.setItem('dash_selected_sectors', JSON.stringify(val)); } catch {}
    });
    sectorsByIndex.subscribe(val => {
        try { sessionStorage.setItem('dash_sectors_by_index', JSON.stringify(val)); } catch {}
    });
}

// --- DATA STORES ---

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

// --- FETCH HELPERS ---

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

// --- DATA LOADERS ---

// retries with backoff because backend may still be loading index data from BigQuery
export async function loadSummaryData(index = 'stoxx50') {
    if (!browser) return;
    summaryData.update(s => ({ ...s, loading: true, error: null }));

    const maxRetries = 5;
    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const data = await fetchWithRetry(
                `${API_BASE_URL}/summary?index=${index}&t=${Date.now()}`
            );
            if (data && data.length > 0) {
                summaryData.set({ assets: data, loaded: true, loading: false, error: null });
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
            summaryData.set({ assets: [], loaded: false, loading: false, error: error.message });
            return;
        }
    }
    summaryData.set({ assets: [], loaded: true, loading: false, error: null });
}

export async function loadIndexOverviewData() {
    if (!browser) return;
    indexOverviewData.update(s => ({ ...s, loading: true }));
    try {
        const data = await fetchWithRetry(
            `${API_BASE_URL}/index-prices/summary?t=${Date.now()}`
        );
        indexOverviewData.set({ assets: data || [], loaded: true, loading: false });
        return data;
    } catch (error) {
        indexOverviewData.set({ assets: [], loaded: false, loading: false });
    }
}

export async function loadRankingsData(period = '1y', index = 'stoxx50') {
    if (!browser) return;
    rankingsData.update(s => ({ ...s, loading: true, error: null }));
    try {
        const data = await fetchWithRetry(
            `${API_BASE_URL}/rankings?period=${period}&index=${index}&t=${Date.now()}`
        );
        rankingsData.set({ rankings: data, period, loaded: true, loading: false, error: null });
        return data;
    } catch (error) {
        rankingsData.set({
            rankings: { selected: { top: [], bottom: [] } },
            period, loaded: false, loading: false, error: error.message,
        });
    }
}

// --- FOCUS REQUEST ---
// triggers sidebar scroll+expand even if the same symbol is re-clicked

export const focusSymbolRequest = writable({ symbol: '', seq: 0 });
export function requestFocusSymbol(symbol) {
    focusSymbolRequest.update(v => ({ symbol, seq: v.seq + 1 }));
}
