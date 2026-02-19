<!--
  Sidebar — Dropdown index selector with West/East groups
-->

<script>
    import { onMount } from 'svelte';
    import { selectedSymbol, summaryData, loadSummaryData, marketIndex, currentCurrency, INDEX_CONFIG, INDEX_GROUPS } from '$lib/stores.js';

    let searchQuery = $state('');
    let lastPriceDate = $state('—');
    let dropdownOpen = $state(false);
    let scrollContainer;

    let tickers = $derived($summaryData.assets || []);
    let loading = $derived($summaryData.loading);
    let error = $derived($summaryData.error);
    let currentIndex = $derived($marketIndex);
    let ccy = $derived($currentCurrency);
    let currentConfig = $derived(INDEX_CONFIG[currentIndex]);

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

    // Scroll selected symbol to center of list instantly
    $effect(() => {
        const sym = $selectedSymbol;
        if (!scrollContainer || !sym) return;
        // Small delay so DOM updates first
        setTimeout(() => {
            const el = scrollContainer.querySelector(`[data-symbol="${CSS.escape(sym)}"]`);
            if (el) {
                const containerRect = scrollContainer.getBoundingClientRect();
                const elRect = el.getBoundingClientRect();
                const offset = elRect.top - containerRect.top - (containerRect.height / 2) + (elRect.height / 2);
                scrollContainer.scrollTop += offset;
            }
        }, 30);
    });

    // Close dropdown on outside click
    function handleClickOutside(e) {
        if (dropdownOpen && !e.target.closest('.index-dropdown')) {
            dropdownOpen = false;
        }
    }

    onMount(() => {
        if (!$summaryData.loaded && !$summaryData.loading) loadSummaryData($marketIndex);
        document.addEventListener('click', handleClickOutside);
        return () => document.removeEventListener('click', handleClickOutside);
    });

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
        dropdownOpen = false;
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

    <div class="p-4 space-y-3 bg-gradient-to-b from-white/5 to-transparent">

        <!-- INDEX DROPDOWN -->
        <div class="relative index-dropdown">
            <button
                onclick={() => { dropdownOpen = !dropdownOpen; }}
                class="w-full flex items-center justify-between px-4 py-2.5 bg-black/40 border border-bloom-muted/20 rounded-xl hover:border-bloom-accent/40 transition-all"
            >
                <div class="flex items-center gap-3">
                    <span class="text-lg font-black text-bloom-accent">{currentConfig?.currency}</span>
                    <div class="text-left">
                        <div class="text-sm font-black text-white uppercase tracking-tight">{currentConfig?.shortLabel}</div>
                        <div class="text-[10px] font-bold text-white/30 uppercase tracking-wider">{currentConfig?.region === 'west' ? 'Western Markets' : 'Eastern Markets'}</div>
                    </div>
                </div>
                <svg class="w-4 h-4 text-white/40 transition-transform {dropdownOpen ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
            </button>

            {#if dropdownOpen}
                <div class="absolute top-full left-0 right-0 mt-1 bg-[#16161e] border border-white/10 rounded-xl shadow-2xl z-50 overflow-hidden backdrop-blur-xl">
                    {#each INDEX_GROUPS as group}
                        <div class="px-3 pt-2.5 pb-1">
                            <span class="text-[9px] font-black text-white/20 uppercase tracking-[0.3em]">{group.label}</span>
                        </div>
                        {#each group.indices as idx}
                            <button
                                onclick={() => switchIndex(idx.key)}
                                class="w-full flex items-center gap-3 px-4 py-2 hover:bg-white/5 transition-all
                                {currentIndex === idx.key ? 'bg-bloom-accent/10' : ''}"
                            >
                                <span class="text-sm font-black text-bloom-accent w-5">{idx.currency}</span>
                                <span class="text-sm font-bold text-white">{idx.shortLabel}</span>
                                {#if currentIndex === idx.key}
                                    <div class="ml-auto w-1.5 h-1.5 rounded-full bg-bloom-accent"></div>
                                {/if}
                            </button>
                        {/each}
                    {/each}
                </div>
            {/if}
        </div>

        <div class="flex justify-between items-end">
            <div>
                <h2 class="text-[10px] font-black text-bloom-accent uppercase tracking-[0.25em] mb-0.5">EQUITY PERFORMANCE</h2>
                <div class="text-xl font-black text-white tracking-tighter uppercase">{currentConfig?.label || 'INDEX'}</div>
            </div>
            <div class="text-right">
                <div class="text-[10px] font-bold text-bloom-text/40 uppercase tracking-widest">Updated</div>
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

    <div bind:this={scrollContainer} class="flex-1 overflow-y-auto custom-scrollbar">
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
                    data-symbol={item.symbol}
                    onclick={() => selectTicker(item.symbol)}
                    class="w-full px-3 py-3 flex items-center border-b border-white/5 hover:bg-white/5 transition-all relative overflow-hidden group
                    {$selectedSymbol === item.symbol ? 'bg-bloom-accent/10' : ''}"
                >
                    {#if $selectedSymbol === item.symbol}
                        <div class="absolute left-0 top-0 bottom-0 w-1 bg-bloom-accent shadow-[0_0_15px_rgba(168,85,247,0.5)]"></div>
                    {/if}

                    <div class="w-[30%] text-left overflow-hidden pl-1">
                        <div class="font-black text-white text-sm tracking-tight group-hover:text-bloom-accent transition-colors">{item.symbol}</div>
                        <div class="text-[10px] font-bold text-bloom-text/30 uppercase tracking-wide truncate pr-1">{item.name || 'Equity'}</div>
                    </div>

                    <div class="w-[26%] text-right pr-2">
                        <div class="text-[13px] font-mono font-black text-white leading-tight">
                            {ccy}{(item.last_price ?? 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                        </div>
                        <div class="text-[11px] font-bold flex items-center justify-end gap-1 {(item.daily_change_pct ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'}">
                            <span>{(item.daily_change_pct ?? 0) >= 0 ? '▲' : '▼'}</span>
                            <span>{Math.abs(item.daily_change_pct ?? 0).toFixed(2)}%</span>
                        </div>
                    </div>

                    <div class="w-[27%] text-right font-mono text-[11px] leading-tight space-y-0.5">
                        <div class="flex justify-end gap-1.5 text-white/40">
                            <span class="font-bold">H</span>
                            <span class="text-white/70 font-bold">{(item.high ?? 0).toFixed(2)}</span>
                        </div>
                        <div class="flex justify-end gap-1.5 text-white/40">
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