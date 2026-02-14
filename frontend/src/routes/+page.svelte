<script>
    import { selectedSymbol } from '$lib/stores.js';
    import Sidebar from '$lib/components/Sidebar.svelte';
    import Chart from '$lib/components/Chart.svelte';
    import RankingPanel from '$lib/components/RankingPanel.svelte';
    import LiveIndicators from '$lib/components/LiveIndicators.svelte';
    import { onMount } from 'svelte';

    // Cloud Run URL
    const API_BASE_URL = 'https://exchange-api-32125618847.europe-west3.run.app';

    let stockData = $state([]);
    let assets = $state([]); 
    let currentPeriod = $state('1y');

    let activeAsset = $derived(
        assets.find(a => a.symbol === $selectedSymbol) || { name: $selectedSymbol }
    );

    async function fetchAssets() {
        try {
            const res = await fetch(`${API_BASE_URL}/summary`);
            if (res.ok) assets = await res.json();
        } catch (e) {
            console.error("Summary fetch error:", e);
        }
    }

    async function fetchStockData(symbol, period) {
        if (!symbol) return;
        try {
            const res = await fetch(`${API_BASE_URL}/data/${encodeURIComponent(symbol)}?period=${period}`);
            stockData = res.ok ? await res.json() : [];
        } catch (e) {
            console.error("Chart fetch error:", e);
            stockData = [];
        }
    }

    onMount(() => {
        fetchAssets();
    });

    $effect(() => {
        fetchStockData($selectedSymbol, currentPeriod);
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