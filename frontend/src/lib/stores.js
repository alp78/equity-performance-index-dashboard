// $lib/stores.js
import { writable } from 'svelte/store';
import { browser } from '$app/environment';

// --- TIMEOUT HELPER ---
async function fetchWithTimeout(url, timeout = 10000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    
    try {
        const response = await fetch(url, { signal: controller.signal });
        clearTimeout(timeoutId);
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            throw new Error(`Request timeout after ${timeout}ms`);
        }
        throw error;
    }
}

// --- RETRY HELPER ---
async function fetchWithRetry(url, retries = 2, timeout = 10000) {
    let lastError;
    
    for (let i = 0; i < retries; i++) {
        try {
            const response = await fetchWithTimeout(url, timeout);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            return await response.json();
        } catch (error) {
            lastError = error;
            console.warn(`Attempt ${i + 1}/${retries} failed for ${url}:`, error.message);
            
            // Don't wait after last attempt
            if (i < retries - 1) {
                // Wait 1 second before retry
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        }
    }
    
    throw lastError;
}

// --- EXISTING STORES ---
// Global symbol selection
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
        const data = await fetchWithRetry(`${backendUrl}/summary`, 2, 10000);
        
        summaryData.set({
            assets: data || [],
            loaded: true,
            loading: false,
            error: null
        });
        
        return data;
    } catch (error) {
        console.error('Summary load failed after retries:', error);
        summaryData.set({
            assets: [],
            loaded: false,
            loading: false,
            error: error.message
        });
        // Don't throw - let component handle error state
    }
}

// --- LOAD RANKINGS FUNCTION (Called when period changes) ---
export async function loadRankingsData(backendUrl, period = '1y') {
    // Only run in browser, not during SSR
    if (!browser) return;
    
    rankingsData.update(state => ({ ...state, loading: true, error: null }));
    
    try {
        const data = await fetchWithRetry(`${backendUrl}/rankings?period=${period}`, 2, 10000);
        
        rankingsData.set({
            rankings: data || { selected: { top: [], bottom: [] } },
            period: period,
            loaded: true,
            loading: false,
            error: null
        });
        
        return data;
    } catch (error) {
        console.error('Rankings load failed after retries:', error);
        rankingsData.set({
            rankings: { selected: { top: [], bottom: [] } },
            period: period,
            loaded: false,
            loading: false,
            error: error.message
        });
        // Don't throw - let component handle error state
    }
}