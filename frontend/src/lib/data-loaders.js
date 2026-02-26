/**
 * =========================================================================
 *  Data Stores & API Loaders
 * =========================================================================
 *  Reactive stores that hold the data returned by the backend API.
 *  Each loader uses a stale-while-revalidate (SWR) strategy: serve
 *  cached data instantly to the UI, then refresh in the background.
 *  Retries with exponential backoff handle the case where the backend
 *  is still loading index data from BigQuery on cold start.
 * =========================================================================
 */

import { writable } from 'svelte/store';
import { browser } from '$app/environment';
import { API_BASE_URL } from '$lib/config.js';
import { getCached, setCached, isCacheFresh } from '$lib/cache.js';


// ── Data Stores ──────────────────────────────────────────────────────────

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
