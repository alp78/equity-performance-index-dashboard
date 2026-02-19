/**
 * Global Stores & Data Loaders
 * ============================
 * INDEX_CONFIG is the single source of truth for all indices.
 * To add a new index, add an entry here — everything else adapts.
 */

import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';
import { API_BASE_URL } from '$lib/config.js';

// --- INDEX CONFIGURATION ---

export const INDEX_CONFIG = {
    // --- WEST ---
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
    // --- EAST ---
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

// Grouped for dropdown
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

// --- GLOBAL STORES ---

export const selectedSymbol = writable('ASML.AS');
export const marketIndex = writable('stoxx50');

export const currentCurrency = derived(marketIndex, ($idx) =>
    INDEX_CONFIG[$idx]?.currency || '$'
);

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

// --- FETCH HELPER ---

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

export async function loadSummaryData(index = 'stoxx50') {
    if (!browser) return;
    summaryData.update(s => ({ ...s, loading: true, error: null }));

    // Retry with backoff — backend may be lazy-loading the index from BigQuery
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
            // Empty = backend still loading, retry
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