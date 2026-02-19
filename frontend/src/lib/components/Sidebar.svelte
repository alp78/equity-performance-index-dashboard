<!--
  Sidebar Component
  =================
  Compact ticker rows. Currency symbol from INDEX_CONFIG.
-->

<script>
    import { onMount } from 'svelte';
    import { selectedSymbol, summaryData, loadSummaryData, marketIndex, currentCurrency, INDEX_CONFIG } from '$lib/stores.js';

    let searchQuery = $state('');
    let lastPriceDate = $state('—');

    let tickers = $derived($summaryData.assets || []);
    let loading = $derived($summaryData.loading);
    let error = $derived($summaryData.error);
    let currentIndex = $derived($marketIndex);
    let ccy = $derived($currentCurrency);

    let filteredTickers = $derived(
        tickers
            .filter(t =>
                t.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
                (t.name && t.name.toLowerCase().includes(searchQuery.toLowerCase()))
            )
            .sort((a, b) => a.symbol.localeCompare(b.symbol))
    );

    $effect(() => {
        if (tickers.length > 0) lastPriceDate = new Date().toLocaleDateString('en-GB');
    });

    onMount(() => {
        if (!$summaryData.loaded && !$summaryData.loading) loadSummaryData($marketIndex);
    });

    // Build index options from INDEX_CONFIG
    const INDEX_OPTIONS = Object.entries(INDEX_CONFIG).map(([key, cfg]) => ({
        key,
        label: cfg.shortLabel,
    }));

    let lastSymbolPerIndex = Object.fromEntries(
        Object.entries(INDEX_CONFIG).map(([key, cfg]) => [key, cfg.defaultSymbol])
    );

    function selectTicker(symbol) {
        selectedSymbol.set(symbol);
        lastSymbolPerIndex[$marketIndex] = symbol;
    }

    async function switchIndex(key) {
        if (key === currentIndex) return;
        lastSymbolPerIndex[currentIndex] = $selectedSymbol;
        marketIndex.set(key);
        searchQuery = '';
        await loadSummaryData(key);
        const remembered = lastSymbolPerIndex[key];
        selectedSymbol.set(remembered || INDEX_CONFIG[key]?.defaultSymbol || '');
    }

    function formatVol(val) {
        if (!val) return '0';
        if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
        if (val >= 1000) return (val / 1000).toFixed(0) + 'K';
        return val.toString();
    }
</script>

<aside class="flex flex-col h-full bg-bloom-card border-r border-bloom-muted/10 relative z-20 shadow-2xl overflow-hidden">

    <div class="p-5 space-y-3 bg-gradient-to-b from-white/5 to-transparent">

        <div class="flex gap-2 p-1 bg-black/40 rounded-xl border border-white/5">
            {#each INDEX_OPTIONS as opt}
                <button
                    onclick={() => switchIndex(opt.key)}
                    class="flex-1 py-2 px-3 rounded-lg text-center transition-all duration-300 relative
                    {currentIndex === opt.key
                        ? 'bg-bloom-accent text-white shadow-[0_0_20px_rgba(168,85,247,0.4)] font-black'
                        : 'text-white/40 hover:text-white/70 hover:bg-white/5 font-bold'}"
                >
                    <span class="text-xs uppercase tracking-widest">{opt.label}</span>
                </button>
            {/each}
        </div>

        <div class="flex justify-between items-end">
            <div>
                <h2 class="text-xs font-black text-bloom-accent uppercase tracking-[0.3em] mb-0.5">EQUITY PERFORMANCE INDEX</h2>
                <div class="text-2xl font-black text-white tracking-tighter uppercase">
                    {INDEX_CONFIG[currentIndex]?.label || 'INDEX'}
                </div>
            </div>
            <div class="text-right">
                <div class="text-[10px] font-bold text-bloom-text/40 uppercase tracking-widest">Last Update</div>
                <div class="text-xs font-mono font-bold text-bloom-accent">{lastPriceDate}</div>
            </div>
        </div>

        <div class="relative group">
            <input
                type="text"
                bind:value={searchQuery}
                placeholder="Search symbol or company name..."
                class="w-full bg-black/40 border border-bloom-muted/20 rounded-xl py-2.5 px-4 pl-10 text-sm font-bold text-white placeholder:text-bloom-text/20 focus:outline-none focus:border-bloom-accent/50 focus:ring-4 focus:ring-bloom-accent/10 transition-all"
            />
            <svg class="absolute left-3 top-3 w-4 h-4 text-bloom-text/20 group-focus-within:text-bloom-accent transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
        </div>
    </div>

    <div class="flex-1 overflow-y-auto custom-scrollbar">
        {#if loading}
            <div class="flex flex-col items-center justify-center h-40 space-y-3 opacity-30">
                <div class="w-6 h-6 border-2 border-bloom-accent border-t-transparent rounded-full animate-spin"></div>
                <span class="text-[10px] font-black uppercase tracking-widest text-white">Loading Tape</span>
            </div>
        {:else if error}
            <div class="flex flex-col items-center justify-center h-40 space-y-3 px-6">
                <div class="text-center space-y-2">
                    <div class="text-red-500 text-sm font-bold">Failed to load market data</div>
                    <div class="text-bloom-text/40 text-xs font-mono">{error}</div>
                </div>
                <button onclick={() => loadSummaryData(currentIndex)} class="px-4 py-2 bg-bloom-accent/20 hover:bg-bloom-accent/30 border border-bloom-accent/50 rounded-lg text-white text-xs font-bold uppercase tracking-wider transition-all">Retry</button>
            </div>
        {:else}
            {#each filteredTickers as item}
                <button
                    onclick={() => selectTicker(item.symbol)}
                    class="w-full px-5 py-3 flex items-center border-b border-white/5 hover:bg-white/5 transition-all relative overflow-hidden group
                    {$selectedSymbol === item.symbol ? 'bg-bloom-accent/10' : ''}"
                >
                    {#if $selectedSymbol === item.symbol}
                        <div class="absolute left-0 top-0 bottom-0 w-1 bg-bloom-accent shadow-[0_0_15px_rgba(168,85,247,0.5)]"></div>
                    {/if}

                    <div class="w-[32%] text-left overflow-hidden">
                        <div class="font-black text-white text-sm tracking-tight group-hover:text-bloom-accent transition-colors">{item.symbol}</div>
                        <div class="text-[10px] font-bold text-bloom-text/30 uppercase tracking-wide truncate pr-2">{item.name || 'Equity'}</div>
                    </div>

                    <div class="w-[25%] text-right pr-3">
                        <div class="text-[13px] font-mono font-black text-white leading-tight">
                            {ccy}{(item.last_price ?? 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                        </div>
                        <div class="text-[11px] font-bold flex items-center justify-end gap-1 {(item.daily_change_pct ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'}">
                            <span>{(item.daily_change_pct ?? 0) >= 0 ? '▲' : '▼'}</span>
                            <span>{Math.abs(item.daily_change_pct ?? 0).toFixed(2)}%</span>
                        </div>
                    </div>

                    <div class="w-[26%] text-right font-mono text-[11px] leading-tight space-y-0.5">
                        <div class="flex justify-end gap-2 text-white/40">
                            <span class="font-bold">H</span>
                            <span class="text-white/70 font-bold">{(item.high ?? 0).toFixed(2)}</span>
                        </div>
                        <div class="flex justify-end gap-2 text-white/40">
                            <span class="font-bold">L</span>
                            <span class="text-white/70 font-bold">{(item.low ?? 0).toFixed(2)}</span>
                        </div>
                    </div>

                    <div class="w-[17%] text-right">
                        <div class="text-[9px] font-black text-bloom-text/30 uppercase tracking-tighter">Vol</div>
                        <div class="text-[11px] font-bold text-white/50">{formatVol(item.volume)}</div>
                    </div>
                </button>
            {/each}
        {/if}
    </div>
</aside>

<style>
    .custom-scrollbar::-webkit-scrollbar { width: 4px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.05); border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(168,85,247,0.3); }
</style>