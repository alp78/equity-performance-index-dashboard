<script>
    // --- IMPORTS ---
    // 'selectedSymbol' is a global store. When the sidebar updates it, this page reacts automatically.
    import { selectedSymbol } from '$lib/stores.js';
    
    // Components
    import Sidebar from '$lib/components/Sidebar.svelte';
    import Chart from '$lib/components/Chart.svelte';
    import RankingPanel from '$lib/components/RankingPanel.svelte';
    import LiveIndicators from '$lib/components/LiveIndicators.svelte';
    
    // Svelte Lifecycle & Environment
    import { onMount } from 'svelte';
    import { PUBLIC_BACKEND_URL } from '$env/static/public';

    // --- STATE MANAGEMENT (SVELTE 5 RUNES) ---
    
    // 1. fullStockData: The "Master" dataset. 
    // We fetch the ENTIRE history ('max') from the backend and store it here.
    let fullStockData = $state([]); 
    
    // 2. stockData: The "View" dataset.
    // This is the sliced subset passed to the Chart. It updates instantly when 'currentPeriod' changes.
    let stockData = $state([]);     
    
    // 3. Metadata for the header (Company Name, etc.)
    let assets = $state([]); 
    
    // 4. UI State
    let currentPeriod = $state('1y');

    // --- DERIVED STATE ---
    // Automatically recalculates 'activeAsset' whenever 'assets' or '$selectedSymbol' changes.
    // Logic: Look up the full object (e.g., "NVDA" -> "NVIDIA Corp") from the summary list.
    let activeAsset = $derived(
        assets.find(a => a.symbol === $selectedSymbol) || { name: $selectedSymbol }
    );

    // --- DATA FETCHING: METADATA ---
    // Fetches the list of all available stocks (Ticker + Name + Daily Change)
    async function fetchAssets() {
        try {
            const res = await fetch(`${PUBLIC_BACKEND_URL}/summary`);
            if (res.ok) assets = await res.json();
        } catch (e) {
            console.error("Summary fetch error:", e);
        }
    }

    // --- DATA FETCHING: HISTORICAL DATA ---
    // STRATEGY: "Heavy Load Once, Zero Latency Later"
    // Instead of asking the server for "1 Month" of data, we ask for "MAX".
    // This takes slightly longer (ms) to download but allows instant period switching later.
    async function fetchStockData(symbol) {
        if (!symbol) return;
        try {
            const res = await fetch(`${PUBLIC_BACKEND_URL}/data/${encodeURIComponent(symbol)}?period=max`);
            fullStockData = res.ok ? await res.json() : [];
        } catch (e) {
            console.error("Chart fetch error:", e);
            fullStockData = [];
        }
    }

    // --- LIFECYCLE: MOUNT ---
    // Runs once when the app loads.
    onMount(() => {
        fetchAssets();
    });

    // --- REACTIVITY: SYMBOL CHANGE ---
    // Triggers whenever the user clicks a stock in the Sidebar ($selectedSymbol changes).
    $effect(() => {
        fullStockData = []; // Clear old data immediately to prevent "ghosting"
        fetchStockData($selectedSymbol);
    });

    // --- REACTIVITY: CLIENT-SIDE FILTERING ---
    // Triggers when either 'fullStockData' arrives OR 'currentPeriod' changes.
    // This logic slices the array in memory (JavaScript) rather than making a network request.
    $effect(() => {
        // Guard clause: If no data yet, do nothing
        if (fullStockData.length === 0) {
            stockData = [];
            return;
        }

        // Case A: User wants everything. Pass the raw Master array.
        if (currentPeriod === 'max') {
            stockData = fullStockData;
        } 
        // Case B: User wants a slice (e.g., '1y').
        else {
            // 1. Find the most recent date in the dataset
            const lastItem = fullStockData[fullStockData.length - 1];
            if (!lastItem || !lastItem.time) {
                stockData = fullStockData;
                return;
            }
            
            // 2. Calculate the "Cutoff Date"
            const lastDate = new Date(lastItem.time);
            const cutoff = new Date(lastDate);
            
            // Map the button labels to days
            const daysMap = { 
                '1w': 7, 
                '1mo': 30, 
                '3mo': 90, 
                '6mo': 180, 
                '1y': 365, 
                '5y': 1825 
            };
            
            const daysToSubtract = daysMap[currentPeriod] || 365;
            cutoff.setDate(cutoff.getDate() - daysToSubtract);
            
            // 3. Format to YYYY-MM-DD for string comparison
            const cutoffStr = cutoff.toISOString().split('T')[0];
            
            // 4. FILTER: Keep only rows newer than the cutoff
            // This happens in <1ms for ~5000 rows.
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