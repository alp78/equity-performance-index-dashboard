<!--
  Main Dashboard Page
-->

<script>
    import { selectedSymbol, loadSummaryData, marketIndex, currentCurrency, INDEX_CONFIG, summaryData } from '$lib/stores.js';
    import { API_BASE_URL } from '$lib/config.js';
    import Sidebar from '$lib/components/Sidebar.svelte';
    import Chart from '$lib/components/Chart.svelte';
    import RankingPanel from '$lib/components/RankingPanel.svelte';
    import LiveIndicators from '$lib/components/LiveIndicators.svelte';
    import { onMount, untrack } from 'svelte';

    async function fetchWithTimeout(url, timeout = 10000) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);
        try {
            const response = await fetch(url, { signal: controller.signal });
            clearTimeout(timeoutId);
            return response;
        } catch (error) {
            clearTimeout(timeoutId);
            if (error.name === 'AbortError') throw new Error(`Timeout`);
            throw error;
        }
    }

    let fullStockData = $state([]);
    let currentPeriod = $state('1y');
    let isInitialLoading = $state(true);
    let isIndexSwitching = $state(false);

    // Name cache: plain object, not reactive
    let metadataCache = {};
    let metadataFetchId = 0;
    let lastFetchedSymbol = '';
    let lastFetchedIndex = '';
    let displayNameText = $state('');

    let currentSymbol = $derived($selectedSymbol);
    let ccy = $derived($currentCurrency);
    // Track assets from the summary store — updates when index switches
    let assets = $derived($summaryData.assets || []);

    let selectMode = $state(false);
    let customRange = $state(null);

    // Display name: metadata → assets → empty (NOT symbol fallback — that's the h1's job)
    let displayName = $derived((() => {
        if (displayNameText) return displayNameText;
        const asset = assets.find(a => a.symbol === currentSymbol);
        if (asset && asset.name && asset.name !== 0 && asset.name !== currentSymbol) return asset.name;
        return '';
    })());

    function fmtDate(d) {
        if (!d) return '';
        const dt = new Date(d + 'T00:00:00');
        const day = dt.getDate();
        const mon = dt.toLocaleDateString('en-GB', { month: 'short' });
        const yr = String(dt.getFullYear()).slice(2);
        return `${day} ${mon} '${yr}`;
    }

    function handleResetPeriod() {
        if (customRange) { customRange = null; selectMode = false; }
        const p = currentPeriod || '1y';
        currentPeriod = null;
        setTimeout(() => { currentPeriod = p; }, 10);
    }

    function setPeriod(p) {
        currentPeriod = p;
        customRange = null;
        selectMode = false;
    }

    function toggleCustomMode() {
        if (selectMode && customRange) { selectMode = false; customRange = null; return; }
        if (selectMode) { selectMode = false; return; }
        selectMode = true;
        customRange = null;
    }

    function handleRangeSelect(range) {
        customRange = range;
        selectMode = false;
        currentPeriod = null;
    }

    async function fetchStockData(symbol) {
        if (!symbol) return;

        // Fetch metadata — set displayNameText on success
        const fetchId = ++metadataFetchId;
        fetchWithTimeout(`${API_BASE_URL}/metadata/${encodeURIComponent(symbol)}`, 8000)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.name && fetchId === metadataFetchId) {
                    metadataCache[symbol] = data.name;
                    displayNameText = data.name;
                }
            })
            .catch(() => {});

        // Retry up to 5 times with backoff — backend may be lazy-loading the index
        const maxRetries = 5;
        for (let attempt = 0; attempt < maxRetries; attempt++) {
            try {
                const res = await fetchWithTimeout(
                    `${API_BASE_URL}/data/${encodeURIComponent(symbol)}?period=max&t=${Date.now()}`, 30000
                );
                if (res.ok) {
                    const json = await res.json();
                    if (json && json.length > 0) {
                        fullStockData = json;
                        isInitialLoading = false;
                        isIndexSwitching = false;
                        return;
                    }
                    // Empty response = backend still loading index, retry
                    if (attempt < maxRetries - 1) {
                        await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
                        continue;
                    }
                }
            } catch (e) {
                if (attempt < maxRetries - 1) {
                    await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
                    continue;
                }
                console.error(`Failed to fetch data for ${symbol}:`, e);
            }
        }
        isInitialLoading = false;
        isIndexSwitching = false;
    }

    onMount(async () => {
        lastFetchedIndex = $marketIndex;
        lastFetchedSymbol = $selectedSymbol;

        // Load sidebar data (with retry)
        await loadSummaryData($marketIndex);

        // Load chart data
        await fetchStockData($selectedSymbol);
        isInitialLoading = false;
    });

    // React to INDEX changes
    $effect(() => {
        const idx = $marketIndex;
        if (idx === lastFetchedIndex) return;
        lastFetchedIndex = idx;
        fullStockData = [];
        isIndexSwitching = true;
        displayNameText = '';
        // Force symbol re-fetch by clearing the guard
        lastFetchedSymbol = '';
    });

    // React to SYMBOL changes
    $effect(() => {
        const sym = $selectedSymbol;
        if (!sym) return;
        if (sym === lastFetchedSymbol) return;
        lastFetchedSymbol = sym;
        displayNameText = metadataCache[sym] || '';
        fullStockData = [];
        selectMode = false;
        fetchStockData(sym);
    });

    $effect(() => {
        const period = currentPeriod;
        // Don't block on isInitialLoading — RankingPanel handles its own loading
    });
</script>

<div class="flex h-screen w-screen bg-[#0d0d12] text-white overflow-hidden font-sans selection:bg-purple-500/30">

    <div class="w-[420px] h-full shrink-0 z-20 shadow-2xl shadow-black/50">
        <Sidebar />
    </div>

    <main class="flex-1 flex flex-col p-6 gap-6 h-screen overflow-hidden relative min-w-0">

        <header class="flex shrink-0 justify-between items-center z-10">
            <div>
                <h1 class="text-5xl font-black text-white uppercase tracking-tighter drop-shadow-lg leading-none">{currentSymbol}</h1>
                <span class="text-sm font-bold uppercase tracking-[0.2em] pl-1 {displayName ? 'text-white/40' : 'text-white/15'}">
                    {displayName || 'Loading...'}
                </span>
            </div>

            <div class="flex items-center gap-3">
                <div class="flex items-center gap-2 bg-white/5 border border-white/10 p-1 rounded-xl shadow-2xl backdrop-blur-md">
                    <button
                        onclick={toggleCustomMode}
                        class="px-4 py-1.5 text-[10px] font-black rounded-lg transition-all duration-300
                        {selectMode
                            ? 'bg-orange-500 text-white shadow-[0_0_15px_rgba(249,115,22,0.4)] animate-pulse'
                            : customRange
                                ? 'bg-orange-500/20 text-orange-400 shadow-[0_0_10px_rgba(249,115,22,0.2)]'
                                : 'text-white/40 hover:bg-white/5 hover:text-white'}"
                    >
                        {selectMode ? '⊞ SELECTING...' : 'CUSTOM RANGE'}
                    </button>
                    {#if customRange}
                        <span class="text-[10px] font-bold text-orange-400/80 tabular-nums whitespace-nowrap pr-2">
                            {fmtDate(customRange.start)} → {fmtDate(customRange.end)}
                        </span>
                    {/if}
                </div>

                <div class="flex bg-white/5 border border-white/10 p-1 rounded-xl shadow-2xl backdrop-blur-md">
                    {#each ['1W', '1MO', '3MO', '6MO', '1Y', '5Y', 'MAX'] as p}
                        <button
                            onclick={() => setPeriod(p.toLowerCase())}
                            class="px-4 py-1.5 text-[10px] font-black rounded-lg transition-all duration-300
                            {currentPeriod === p.toLowerCase()
                                ? 'bg-purple-500 text-white shadow-[0_0_15px_rgba(168,85,247,0.4)] scale-105'
                                : 'text-white/40 hover:bg-white/5 hover:text-white'}"
                        >{p}</button>
                    {/each}
                </div>
            </div>
        </header>

        <div class="flex-1 flex flex-col gap-6 min-h-0 min-w-0 z-0">
            <section class="flex-[2] min-h-0 w-full min-w-0 bg-[#111114] rounded-3xl border border-white/5 relative overflow-hidden flex flex-col shadow-2xl
                {selectMode ? 'ring-2 ring-orange-500/30' : ''}">
                <div class="absolute inset-0 bg-gradient-to-b from-white/5 to-transparent pointer-events-none opacity-50"></div>
                {#if (isInitialLoading || isIndexSwitching) && fullStockData.length === 0}
                    <div class="absolute inset-0 flex items-center justify-center z-10">
                        <div class="flex flex-col items-center space-y-3 opacity-30">
                            <div class="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                            <span class="text-[10px] font-black uppercase tracking-widest text-white">Loading Chart</span>
                        </div>
                    </div>
                {:else if fullStockData.length === 0}
                    <div class="absolute inset-0 flex items-center justify-center z-10">
                        <div class="flex flex-col items-center space-y-3 opacity-30">
                            <div class="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                            <span class="text-[10px] font-black uppercase tracking-widest text-white">Loading Data</span>
                        </div>
                    </div>
                {/if}
                <div class="flex-1 min-h-0 min-w-0 chart-no-animate" style="transition: none !important;">
                    <Chart
                        data={fullStockData}
                        {currentPeriod}
                        {selectMode}
                        {customRange}
                        currency={ccy}
                        onResetPeriod={handleResetPeriod}
                        onRangeSelect={handleRangeSelect}
                    />
                </div>
            </section>

            <div class="flex-1 grid grid-cols-12 gap-6 min-h-0">
                <div class="col-span-4 min-h-0 flex flex-col">
                    <RankingPanel {currentPeriod} {customRange} />
                </div>
                <div class="col-span-4 min-h-0">
                    <LiveIndicators title="MARKET LEADERS" symbols={['NVDA', 'AAPL', 'MSFT']} dynamicByIndex={true} />
                </div>
                <div class="col-span-4 min-h-0">
                    <LiveIndicators title="GLOBAL MACRO" subtitle=" " symbols={['BINANCE:BTCUSDT', 'FXCM:XAU/USD', 'FXCM:EUR/USD']} />
                </div>
            </div>
        </div>
    </main>
</div>