<script>
    // --- IMPORTS ---
    // 'selectedSymbol' is a global store. When the sidebar updates it, this page reacts automatically.
    import { selectedSymbol, loadSummaryData, loadRankingsData } from '$lib/stores.js';
    
    // Components
    import Sidebar from '$lib/components/Sidebar.svelte';
    import Chart from '$lib/components/Chart.svelte';
    import RankingPanel from '$lib/components/RankingPanel.svelte';
    import LiveIndicators from '$lib/components/LiveIndicators.svelte';
    
    // Svelte Lifecycle & Environment
    // Added 'untrack' to safely manage the initial load dependency
    import { onMount, untrack } from 'svelte';
    import { PUBLIC_BACKEND_URL } from '$env/static/public';

    // --- STATE MANAGEMENT (SVELTE 5 RUNES) ---
    
    // 1. fullStockData: The "Master" dataset.
    let fullStockData = $state([]); 
    
    // 2. stockData: The "View" dataset passed to the Chart.
    let stockData = $state([]);     
    
    // 3. Metadata for the header.
    let assets = $state([]); 
    
    // 4. UI State
    let currentPeriod = $state('1y');
    
    // 5. Track previous period to detect changes
    let previousPeriod = $state('1y');

    // 6. Initial Load Guard
    // Prevents the "Dark Chart" issue by tracking if we are still in the boot-up phase.
    let isInitialLoading = $state(true);

    // 7. Fast-Track Metadata (Added for instant name loading)
    let currentMetadata = $state({ name: "" });

    // --- DERIVED STATE ---
    let activeAsset = $derived(
        currentMetadata.name 
        ? { name: currentMetadata.name, symbol: $selectedSymbol }
        : (assets.find(a => a.symbol === $selectedSymbol) || { name: $selectedSymbol })
    );

    // --- DATA FETCHING ---
    // OPTIMIZATION: Load summary and rankings separately
    async function fetchInitialData() {
        try {
            // Fetch summary data (sidebar) - only called once
            const summaryResult = await loadSummaryData(PUBLIC_BACKEND_URL);
            assets = summaryResult || [];
            
            // Fetch initial rankings data
            await loadRankingsData(PUBLIC_BACKEND_URL, currentPeriod);
        } catch (e) {
            console.error("Initial data fetch error:", e);
        }
    }

    async function fetchStockData(symbol, period = 'max') {
        if (!symbol) return;
        
        // PARALLEL METADATA FETCH
        // This fires immediately and updates 'currentMetadata' as soon as it returns
        fetch(`${PUBLIC_BACKEND_URL}/metadata/${encodeURIComponent(symbol)}`)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.name) currentMetadata.name = data.name;
            })
            .catch(e => console.error("Metadata fetch error:", e));

        try {
            // ALWAYS fetch full data (max) and filter client-side for responsiveness
            const res = await fetch(`${PUBLIC_BACKEND_URL}/data/${encodeURIComponent(symbol)}?period=max`);
            if (res.ok) {
                fullStockData = await res.json();
                // Critical: Mark initial load as complete so the UI knows we have data
                isInitialLoading = false; 
            } else {
                fullStockData = [];
            }
        } catch (e) {
            console.error("Chart fetch error:", e);
            fullStockData = [];
        }
    }

    // --- LIFECYCLE: MOUNT ---
    // Runs once when the app loads.
    onMount(() => {
        // 1. OPTIMIZATION: Load summary + rankings separately
        fetchInitialData();
        
        // 2. FORCE INITIAL FETCH
        // This solves the issue where AAPL was dark until clicked.
        // We explicitly fetch the default symbol immediately on load.
        fetchStockData($selectedSymbol, currentPeriod);
    });

    // --- REACTIVITY: SYMBOL CHANGE ---
    // Triggers whenever the user clicks a stock in the Sidebar.
    $effect(() => {
        const sym = $selectedSymbol; // Register dependency
        
        // Clear local metadata to ensure the new name is fetched fresh
        untrack(() => { currentMetadata.name = ""; });

        // Use 'untrack' to check the loading state without creating a circular dependency.
        // This ensures we don't double-fetch on the very first render.
        if (untrack(() => isInitialLoading)) return;

        fullStockData = []; // Clear old data to prevent ghosting
        fetchStockData(sym, untrack(() => currentPeriod));
    });

    // --- REACTIVITY: PERIOD CHANGE ---
    // Triggers when period buttons are clicked
    $effect(() => {
        const period = currentPeriod;
        
        // Skip if initial load hasn't completed
        if (untrack(() => isInitialLoading)) return;
        
        // Only reload rankings if period actually changed
        if (period !== untrack(() => previousPeriod)) {
            previousPeriod = period;
            loadRankingsData(PUBLIC_BACKEND_URL, period).catch(console.error);
        }
    });

    // --- REACTIVITY: CLIENT-SIDE FILTERING ---
    // Triggers when data arrives or period changes.
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

<div class="flex h-screen w-full bg-[#0d0d12] text-white overflow-hidden font-sans selection:bg-purple-500/30">
    
    <Sidebar />

    <main class="flex-1 flex flex-col p-6 gap-6 h-screen overflow-hidden relative min-w-0">
        
        <header class="flex shrink-0 justify-between items-center z-10">
            <div>
                <h1 class="text-5xl font-black text-white uppercase tracking-tighter drop-shadow-lg">
                    {$selectedSymbol}
                </h1>
                <span class="text-xs font-bold text-white/30 uppercase tracking-[0.2em] pl-1">
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
                <Chart data={stockData} symbol={$selectedSymbol} companyName={ (activeAsset.name && activeAsset.name !== 0) ? activeAsset.name : $selectedSymbol } />
            </section>
            
            <div class="flex-1 grid grid-cols-12 gap-6 min-h-0">
                
                <div class="col-span-8 grid grid-cols-2 gap-6 min-h-0">
                    <LiveIndicators 
                        title="US EQUITIES" 
                        symbols={['NVDA', 'AAPL', 'MSFT']} 
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