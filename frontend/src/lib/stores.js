/**
 * Global Stores & Data Loaders
 * ============================
 * Central state management for the dashboard.
 *
 * INDEX_CONFIG is the single source of truth for index metadata.
 * To add a new index (e.g. Nikkei, CSI 300), just add an entry here —
 * all components read currency, label, and default symbol from this map.
 */

import { writable, derived, get } from 'svelte/store';
import { browser } from '$app/environment';
import { API_BASE_URL } from '$lib/config.js';

// --- INDEX CONFIGURATION ---
// Modular: add new indices here. Every component reads from this.

export const INDEX_CONFIG = {
    stoxx50: {
        label: 'EURO STOXX 50',
        shortLabel: 'STOXX 50',
        currency: '€',
        currencyCode: 'EUR',
        defaultSymbol: 'ASML.AS',
    },
    sp500: {
        label: 'S&P 500',
        shortLabel: 'S&P 500',
        currency: '$',
        currencyCode: 'USD',
        defaultSymbol: 'NVDA',
    },
    // Future indices:
    // nikkei: {
    //     label: 'Nikkei 225',
    //     shortLabel: 'Nikkei',
    //     currency: '¥',
    //     currencyCode: 'JPY',
    //     defaultSymbol: '7203.T',
    // },
    // csi300: {
    //     label: 'CSI 300',
    //     shortLabel: 'CSI 300',
    //     currency: '¥',
    //     currencyCode: 'CNY',
    //     defaultSymbol: '600519.SS',
    // },
};

// --- GLOBAL STORES ---

export const selectedSymbol = writable('ASML.AS');
export const marketIndex = writable('stoxx50');

// Derived: current currency symbol (reactive, updates when index changes)
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
    try {
        const data = await fetchWithRetry(
            `${API_BASE_URL}/summary?index=${index}&t=${Date.now()}`
        );
        summaryData.set({ assets: data || [], loaded: true, loading: false, error: null });
        return data;
    } catch (error) {
        summaryData.set({ assets: [], loaded: false, loading: false, error: error.message });
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