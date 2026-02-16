// $lib/stores.js
import { writable } from 'svelte/store';
import { browser } from '$app/environment';

// --- EXISTING STORES ---
// Global symbol selection (already exists in your app)
export const selectedSymbol = writable('AAPL');
export const metadata = writable({});

// --- OPTIMIZED: SEPARATE STORES FOR SUMMARY AND RANKINGS ---
// Summary data (sidebar) - loaded once and stays constant
export const summaryData = writable({
    assets: [],
    loaded: false,
    loading: false,
    error: null
});

// Rankings data - changes with period
export const rankingsData = writable({
    rankings: { selected: { top: [], bottom: [] } },
    period: '1y',
    loaded: false,
    loading: false,
    error: null
});

// --- LOAD SUMMARY FUNCTION (Called once on mount) ---
export async function loadSummaryData(backendUrl) {
    // Only run in browser, not during SSR
    if (!browser) return;
    
    summaryData.update(state => ({ ...state, loading: true, error: null }));
    
    try {
        const response = await fetch(`${backendUrl}/summary`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        summaryData.set({
            assets: data || [],
            loaded: true,
            loading: false,
            error: null
        });
        
        return data;
    } catch (error) {
        console.error('Summary load error:', error);
        summaryData.update(state => ({
            ...state,
            loading: false,
            error: error.message
        }));
        throw error;
    }
}

// --- LOAD RANKINGS FUNCTION (Called when period changes) ---
export async function loadRankingsData(backendUrl, period = '1y') {
    // Only run in browser, not during SSR
    if (!browser) return;
    
    rankingsData.update(state => ({ ...state, loading: true, error: null }));
    
    try {
        const response = await fetch(`${backendUrl}/rankings?period=${period}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        rankingsData.set({
            rankings: data || { selected: { top: [], bottom: [] } },
            period: period,
            loaded: true,
            loading: false,
            error: null
        });
        
        return data;
    } catch (error) {
        console.error('Rankings load error:', error);
        rankingsData.update(state => ({
            ...state,
            loading: false,
            error: error.message
        }));
        throw error;
    }
}