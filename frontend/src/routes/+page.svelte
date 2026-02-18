<script>
    /**
     * @file +page.svelte
     * @description Core application orchestrator for the Market Dashboard.
     * Manages global state synchronization, reactive data fetching, 
     * and client-side time-series filtering using Svelte 5 Runes.
     */

    import { selectedSymbol, loadSummaryData, loadRankingsData, marketIndex } from '$lib/stores.js';
    import { API_BASE_URL } from '$lib/config.js';
    import Sidebar from '$lib/components/Sidebar.svelte';
    import Chart from '$lib/components/Chart.svelte';
    import RankingPanel from '$lib/components/RankingPanel.svelte';
    import LiveIndicators from '$lib/components/LiveIndicators.svelte';
    import { onMount, untrack } from 'svelte';

    // --- UTILITIES ---

    /**
     * Standard fetch wrapper with AbortController integration for request timeouts.
     * @param {string} url - The target endpoint.
     * @param {number} timeout - Maximum wait time in milliseconds.
     */
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

    // --- STATE MANAGEMENT (Svelte 5 Runes) ---

    // Master dataset: Stores the full 'max' history fetched from the backend.
    let fullStockData = $state([]); 
    // View dataset: The filtered subset actually passed to the Chart component.
    let stockData = $state([]);     
    // Registry of available assets for the current market index.
    let assets = $state([]); 
    // Active time window (e.g., '1w', '1y') for filtering historical data.
    let currentPeriod = $state('1y');
    // Change tracker to prevent redundant ranking fetches.
    let previousPeriod = $state('1y');
    // Guard to prevent reactive effects from firing during initial boot.
    let isInitialLoading = $state(true);
    // Transient storage for specific asset name and description.
    let currentMetadata = $state({ name: "" });
    // Flag to trigger loading overlays in the chart section.
    let chartLoading = $state(false);

    // --- DERIVED STATE ---

    /**
     * Computes the display object for the active asset.
     * Prioritizes live metadata over the summary registry.
     */
    let activeAsset = $derived(
        currentMetadata.name 
        ? { name: currentMetadata.name, symbol: $selectedSymbol }
        : (assets.find(a => a.symbol === $selectedSymbol) || { name: $selectedSymbol })
    );

    // --- DATA ORCHESTRATION ---

    /**
     * Initial bootstrap: Fetches the market summary and initial ranking set.
     */
    async function fetchInitialData() {
        try {
            const summaryResult = await loadSummaryData($marketIndex);
            assets = summaryResult || [];
            await loadRankingsData(currentPeriod, $marketIndex);
        } catch (e) {
            console.error("Initial data fetch error:", e);
        }
    }

    /**
     * Fetches historical time-series data and asset metadata.
     * Implements a 2-attempt retry logic for resilient network connectivity.
     */
    async function fetchStockData(symbol, period = 'max') {
        if (!symbol) return;
        chartLoading = true;
        
        // Parallel metadata fetch (Fire and forget)
        fetchWithTimeout(`${API_BASE_URL}/metadata/${encodeURIComponent(symbol)}`, 5000)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.name) currentMetadata.name = data.name;
            })
            .catch(e => console.warn("Metadata fetch error:", e));

        let retries = 2;
        let lastError;
        
        // Historical data fetch loop
        for (let i = 0; i < retries; i++) {
            try {
                // Request 'max' period to enable instant client-side filtering later
                const res = await fetchWithTimeout(
                    `${API_BASE_URL}/data/${encodeURIComponent(symbol)}?period=max&t=${Date.now()}`,
                    10000
                );
                if (res.ok) {
                    fullStockData = await res.json();
                    isInitialLoading = false;
                    chartLoading = false;
                    return;
                } else {
                    throw new Error(`HTTP ${res.status}`);
                }
            } catch (e) {
                lastError = e;
                console.warn(`Chart fetch attempt ${i + 1}/${retries} failed:`, e.message);
                if (i < retries - 1) {
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }
            }
        }
        
        console.error("Chart fetch failed after retries:", lastError);
        fullStockData = [];
        chartLoading = false;
    }

    // --- LIFECYCLE & REACTIVE EFFECTS ---

    /**
     * Application Entry Point.
     */
    onMount(() => {
        fetchInitialData();
        fetchStockData($selectedSymbol, currentPeriod);
    });

    /**
     * SYMBOL CHANGE HANDLER
     * Reacts to changes in the global symbol store.
     */
    $effect(() => {
        const sym = $selectedSymbol;
        untrack(() => { currentMetadata.name = ""; });
        if (untrack(() => isInitialLoading)) return;
        fullStockData = []; // Clear state to avoid chart ghosting
        fetchStockData(sym, untrack(() => currentPeriod));
    });

    /**
     * PERIOD CHANGE HANDLER
     * Updates local rankings when the time horizon is toggled.
     */
    $effect(() => {
        const period = currentPeriod;
        if (untrack(() => isInitialLoading)) return;
        if (period !== untrack(() => previousPeriod)) {
            previousPeriod = period;
            loadRankingsData(period, $marketIndex).catch(console.error);
        }
    });

    /**
     * CLIENT-SIDE TIME FILTERING
     * Slices the 'fullStockData' locally to ensure sub-millisecond chart updates.
     */
    $effect(() => {
        if (fullStockData.length === 0) {
            stockData = [];
            return;
        }

        if (currentPeriod === 'max') {
            stockData = fullStockData;
        } else {
            const lastItem = fullStockData[fullStockData.length - 1];
            if (!lastItem || !lastItem.time) {
                stockData = fullStockData;
                return;
            }
            
            const lastDate = new Date(lastItem.time);
            const cutoff = new Date(lastDate);
            
            // Define time-series cutoff intervals
            const daysMap = { 
                '1w': 7, '1mo': 30, '3mo': 90, 
                '6mo': 180, '1y': 365, '5y': 1825 
            };
            
            const daysToSubtract = daysMap[currentPeriod] || 365;
            cutoff.setDate(cutoff.getDate() - daysToSubtract);
            
            const cutoffStr = cutoff.toISOString().split('T')[0];
            stockData = fullStockData.filter(d => d.time >= cutoffStr);
        }
    });
</script>

<div class="flex h-screen w-screen bg-[#0d0d12] text-white overflow-hidden font-sans selection:bg-purple-500/30">
    
    <div class="w-[480px] h-full shrink-0 z-20 shadow-2xl shadow-black/50">
        <Sidebar />
    </div>

    <main class="flex-1 flex flex-col p-6 gap-6 h-screen overflow-hidden relative min-w-0">
        
        <header class="flex shrink-0 justify-between items-center z-10">
            <div>
                <h1 class="text-5xl font-black text-white uppercase tracking-tighter drop-shadow-lg leading-none">{$selectedSymbol}</h1>
                <span class="text-sm font-bold text-white/40 uppercase tracking-[0.2em] pl-1">
                    { (activeAsset.name && activeAsset.name !== 0) ? activeAsset.name : $selectedSymbol }
                </span>
            </div>

            <div class="flex bg-white/5 border border-white/10 p-1 rounded-xl shadow-2xl backdrop-blur-md">
                {#each ['1W', '1MO', '3MO', '6MO', '1Y', '5Y', 'MAX'] as p}
                    <button 
                        onclick={() => currentPeriod = p.toLowerCase()}
                        class="px-4 py-1.5 text-[10px] font-black rounded-lg transition-all duration-300
                        {currentPeriod === p.toLowerCase() 
                            ? 'bg-purple-500 text-white shadow-[0_0_15px_rgba(168,85,247,0.4)] scale-105' 
                            : 'text-white/40 hover:bg-white/5 hover:text-white'}"
                    >
                        {p}
                    </button>
                {/each}
            </div>
        </header>

        <div class="flex-1 flex flex-col gap-6 min-h-0 min-w-0 z-0">
            <section class="flex-[2] min-h-0 w-full min-w-0 bg-[#111114] rounded-3xl border border-white/5 relative overflow-hidden flex flex-col shadow-2xl">
                <div class="absolute inset-0 bg-gradient-to-b from-white/5 to-transparent pointer-events-none opacity-50"></div>
                
                {#if isInitialLoading && fullStockData.length === 0}
                    <div class="absolute inset-0 flex items-center justify-center z-10">
                        <div class="flex flex-col items-center space-y-3 opacity-30">
                            <div class="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                            <span class="text-[10px] font-black uppercase tracking-widest text-white">Loading Chart</span>
                        </div>
                    </div>
                {/if}

                <div class="flex-1 min-h-0 min-w-0 chart-no-animate" style="transition: none !important;">
                    <Chart 
                        data={stockData} 
                        symbol={$selectedSymbol} 
                        companyName={ (activeAsset.name && activeAsset.name !== 0) ? activeAsset.name : $selectedSymbol } 
                    />
                </div>
            </section>
            
            <div class="flex-1 grid grid-cols-12 gap-6 min-h-0">
                <div class="col-span-8 grid grid-cols-2 gap-6 min-h-0">
                    <LiveIndicators 
                        title="US EQUITIES" 
                        symbols={['NVDA', 'AAPL', 'MSFT']} 
                        dynamicByIndex={true}
                    />
                    
                    <LiveIndicators 
                        title="GLOBAL MACRO" 
                        symbols={['BINANCE:BTCUSDT', 'FXCM:XAU/USD', 'FXCM:EUR/USD']} 
                    />
                </div>

                <div class="col-span-4 min-h-0 flex flex-col">
                    <RankingPanel {currentPeriod} />
                </div>
            </div>
        </div>
    </main>
</div>