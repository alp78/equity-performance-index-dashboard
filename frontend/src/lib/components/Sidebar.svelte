<!--
  Sidebar — Sector-grouped stock list with collapsible sections
-->

<script>
    import { onMount } from 'svelte';
    import { selectedSymbol, summaryData, loadSummaryData, marketIndex, currentCurrency, INDEX_CONFIG, INDEX_GROUPS, focusSymbolRequest } from '$lib/stores.js';

    let searchQuery = $state('');
    let dropdownOpen = $state(false);
    let scrollContainer;

    // Per-index open sectors state
    let openSectors = $state(new Set());

    let tickers = $derived($summaryData.assets || []);
    let loading = $derived($summaryData.loading);
    let error = $derived($summaryData.error);
    let currentIndex = $derived($marketIndex);
    let ccy = $derived($currentCurrency);
    let currentConfig = $derived(INDEX_CONFIG[currentIndex]);

    const INDEX_FLAGS = {
        stoxx50:   'fi fi-eu',
        sp500:     'fi fi-us',
        ftse100:   'fi fi-gb',
        nikkei225: 'fi fi-jp',
        csi300:    'fi fi-cn',
        nifty50:   'fi fi-in',
    };

    // Build sector → industry → stocks hierarchy
    let sectorTree = $derived((() => {
        const filtered = tickers.filter(t =>
            !searchQuery ||
            t.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
            (t.name && t.name.toLowerCase().includes(searchQuery.toLowerCase()))
        );
        const sectorMap = {};
        filtered.forEach(t => {
            const sector = (t.sector && t.sector !== 0) ? t.sector : 'Other';
            const industry = (t.industry && t.industry !== 0) ? t.industry : 'General';
            if (!sectorMap[sector]) sectorMap[sector] = {};
            if (!sectorMap[sector][industry]) sectorMap[sector][industry] = [];
            sectorMap[sector][industry].push(t);
        });
        return Object.keys(sectorMap).sort().map(sector => ({
            name: sector,
            industries: Object.keys(sectorMap[sector]).sort().map(industry => ({
                name: industry,
                stocks: sectorMap[sector][industry].sort((a, b) => a.symbol.localeCompare(b.symbol)),
            })),
            stockCount: Object.values(sectorMap[sector]).flat().length,
        }));
    })());

    // Open the sector for a given symbol using current tickers
    function openSectorFor(symbol) {
        const asset = tickers.find(t => t.symbol === symbol);
        if (!asset) return;
        const sectorName = (asset.sector && asset.sector !== 0) ? asset.sector : 'Other';
        if (!openSectors.has(sectorName)) {
            openSectors = new Set([...openSectors, sectorName]);
        }
    }

    // Set sectors to exactly one sector for a symbol
    function resetSectorsTo(symbol, tickerList) {
        const asset = tickerList.find(t => t.symbol === symbol);
        if (!asset) { openSectors = new Set(); return; }
        const sectorName = (asset.sector && asset.sector !== 0) ? asset.sector : 'Other';
        openSectors = new Set([sectorName]);
    }

    // External focus request (from TopMovers/MarketLeaders): open sector + scroll
    // Uses a seq counter so re-clicking same symbol still triggers
    let lastFocusSeq = 0;
    $effect(() => {
        const req = $focusSymbolRequest;
        if (!req.symbol || req.seq === lastFocusSeq) return;
        lastFocusSeq = req.seq;

        if (tickers.length === 0) return;
        openSectorFor(req.symbol);

        if (!scrollContainer) return;
        setTimeout(() => {
            const el = scrollContainer.querySelector(`[data-symbol="${CSS.escape(req.symbol)}"]`);
            if (el) {
                const cr = scrollContainer.getBoundingClientRect();
                const er = el.getBoundingClientRect();
                scrollContainer.scrollTop += er.top - cr.top - (cr.height / 2) + (er.height / 2);
            }
        }, 50);
    });

    function toggleSector(sector) {
        const next = new Set(openSectors);
        next.has(sector) ? next.delete(sector) : next.add(sector);
        openSectors = next;
    }

    function handleClickOutside(e) {
        if (dropdownOpen && !e.target.closest('.index-dropdown')) dropdownOpen = false;
    }

    onMount(async () => {
        document.addEventListener('click', handleClickOutside);
        if (!$summaryData.loaded && !$summaryData.loading) {
            const data = await loadSummaryData($marketIndex);
            if (data && data.length > 0) {
                resetSectorsTo($selectedSymbol, data);
            }
        } else if ($summaryData.assets.length > 0) {
            resetSectorsTo($selectedSymbol, $summaryData.assets);
        }
        return () => document.removeEventListener('click', handleClickOutside);
    });

    let lastSymbolPerIndex = Object.fromEntries(
        Object.entries(INDEX_CONFIG).map(([key, cfg]) => [key, cfg.defaultSymbol])
    );

    function selectTicker(symbol) {
        selectedSymbol.set(symbol);
        lastSymbolPerIndex[$marketIndex] = symbol;
        openSectorFor(symbol);
    }

    async function switchIndex(key) {
        if (key === currentIndex) return;
        lastSymbolPerIndex[currentIndex] = $selectedSymbol;
        openSectors = new Set(); // Clear immediately
        marketIndex.set(key);
        searchQuery = '';
        dropdownOpen = false;
        const newTickers = await loadSummaryData(key);
        const newSymbol = lastSymbolPerIndex[key] || INDEX_CONFIG[key]?.defaultSymbol || '';
        selectedSymbol.set(newSymbol);
        // Set sector AFTER we have the correct tickers
        if (newTickers && newTickers.length > 0) {
            resetSectorsTo(newSymbol, newTickers);
        }
    }

    function formatVol(val) {
        if (!val) return '0';
        if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
        if (val >= 1000) return (val / 1000).toFixed(0) + 'K';
        return val.toString();
    }

    // Exported: open sector for a symbol (from top movers/leaders click)
    export function openSectorForSymbol(symbol) {
        openSectorFor(symbol);
    }
</script>

<aside class="flex flex-col h-full bg-bloom-card border-r border-bloom-muted/10 relative z-20 shadow-2xl overflow-hidden">

    <div class="p-4 space-y-3 bg-gradient-to-b from-white/5 to-transparent">

        <!-- INDEX DROPDOWN -->
        <div class="relative index-dropdown">
            <!-- Dashboard Title -->
            <div class="text-[12px] font-black text-white/25 uppercase tracking-[0.3em] mb-2.5">Global Exchange Monitor</div>

            <button
                onclick={() => dropdownOpen = !dropdownOpen}
                class="w-full flex items-center justify-between bg-black/40 border border-bloom-muted/20 rounded-xl px-4 py-3 hover:border-bloom-accent/40 transition-all group"
            >
                <div class="flex items-center gap-3">
                    <span class="{INDEX_FLAGS[currentIndex] || ''} fis rounded-sm" style="font-size: 1.4rem;"></span>
                    <span class="text-base font-black text-white/85">{currentConfig?.shortLabel}</span>
                    <span class="text-[12px] font-bold text-bloom-accent uppercase ml-1">{currentConfig?.currencyCode}</span>
                    <span class="text-[18px] font-black text-bloom-accent/50">{currentConfig?.currency}</span>
                </div>
                <svg class="w-4 h-4 text-white/30 group-hover:text-bloom-accent transition-all {dropdownOpen ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
            </button>

            {#if dropdownOpen}
                <div class="absolute top-full left-0 right-0 mt-2 bg-[#16161e] border border-white/10 rounded-2xl shadow-2xl z-50 overflow-hidden">
                    {#each INDEX_GROUPS as group}
                        <div class="px-4 py-2 text-[9px] font-black text-white/20 uppercase tracking-widest border-b border-white/5">{group.label}</div>
                        {#each group.indices as idx}
                            <button
                                onclick={() => switchIndex(idx.key)}
                                class="w-full flex items-center justify-between px-4 py-3.5 hover:bg-white/5 transition-all
                                {idx.key === currentIndex ? 'bg-bloom-accent/10 border-l-[3px] border-l-bloom-accent' : 'border-l-[3px] border-l-transparent'}"
                            >
                                <div class="flex items-center gap-3">
                                    <span class="{INDEX_FLAGS[idx.key] || ''} fis rounded-sm" style="font-size: 1.3rem;"></span>
                                    <span class="text-[15px] font-bold text-white/80">{idx.shortLabel}</span>
                                </div>
                                <div class="flex items-center gap-2 pr-2">
                                    <span class="text-[11px] font-bold text-bloom-accent/70">{idx.currencyCode}</span>
                                    <span class="text-[14px] font-black text-bloom-accent/50">{idx.currency}</span>
                                    {#if idx.key === currentIndex}
                                        <span class="ml-1 w-2 h-2 rounded-full bg-bloom-accent shadow-[0_0_6px_rgba(168,85,247,0.6)]"></span>
                                    {/if}
                                </div>
                            </button>
                        {/each}
                    {/each}
                </div>
            {/if}
        </div>

        <!-- SEARCH + EXPAND/COLLAPSE -->
        <div class="flex gap-2">
            <div class="relative group flex-1">
                <input type="text" bind:value={searchQuery} placeholder="Search symbol or name..."
                    class="w-full bg-black/40 border border-bloom-muted/20 rounded-xl py-2.5 px-4 pl-10 text-sm font-bold text-white placeholder:text-bloom-text/20 focus:outline-none focus:border-bloom-accent/50 focus:ring-4 focus:ring-bloom-accent/10 transition-all"
                />
                <svg class="absolute left-3 top-3 w-4 h-4 text-bloom-text/20 group-focus-within:text-bloom-accent transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
            </div>
            <button onclick={() => { openSectors = new Set(sectorTree.map(s => s.name)); }} title="Expand all"
                class="shrink-0 w-9 flex items-center justify-center bg-black/40 border border-bloom-muted/20 rounded-xl hover:border-bloom-accent/40 transition-all text-white/30 hover:text-bloom-accent"
            >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
            </button>
            <button onclick={() => { openSectors = new Set(); }} title="Collapse all"
                class="shrink-0 w-9 flex items-center justify-center bg-black/40 border border-bloom-muted/20 rounded-xl hover:border-bloom-accent/40 transition-all text-white/30 hover:text-bloom-accent"
            >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
                </svg>
            </button>
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
        {:else if searchQuery}
            {#each sectorTree.flatMap(s => s.industries.flatMap(i => i.stocks)) as item}
                {@render stockRow(item)}
            {/each}
        {:else}
            {#each sectorTree as sector}
                <button onclick={() => toggleSector(sector.name)}
                    class="w-full flex items-center justify-between px-4 py-3 bg-[#1a1a24] hover:bg-[#1e1e2a] border-b border-white/[0.06] border-l-[3px] border-l-bloom-accent/50 transition-all sticky top-0 z-10"
                >
                    <div class="flex items-center gap-2.5">
                        <svg class="w-3 h-3 text-white/40 transition-transform {openSectors.has(sector.name) ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                        </svg>
                        <span class="text-[11px] font-black text-white/80 uppercase tracking-widest">{sector.name}</span>
                    </div>
                    <span class="text-[12px] font-bold text-white/25 tabular-nums">{sector.stockCount}</span>
                </button>

                {#if openSectors.has(sector.name)}
                    {#each sector.industries as industry}
                        <div class="flex items-center gap-2 px-4 py-[7px] ml-2 border-l-[2px] border-l-amber-500/30 bg-[#13131a]">
                            <span class="text-[10px] font-semibold text-white/55 uppercase tracking-wide">{industry.name}</span>
                        </div>
                        {#each industry.stocks as item}
                            {@render stockRow(item)}
                        {/each}
                    {/each}
                {/if}
            {/each}
        {/if}
    </div>
</aside>

{#snippet stockRow(item)}
    <button
        data-symbol={item.symbol}
        onclick={() => selectTicker(item.symbol)}
        title="{item.symbol}{item.name ? ' — ' + item.name : ''}"
        class="w-full pl-6 pr-3 py-2.5 flex items-center border-b border-white/[0.03] hover:bg-white/[0.04] transition-all relative overflow-hidden group
        {$selectedSymbol === item.symbol ? 'bg-bloom-accent/10 !border-b-bloom-accent/20' : ''}"
    >
        {#if $selectedSymbol === item.symbol}
            <div class="absolute left-0 top-0 bottom-0 w-1 bg-bloom-accent shadow-[0_0_15px_rgba(168,85,247,0.5)]"></div>
        {/if}
        <div class="w-[28%] text-left overflow-hidden pl-1">
            <div class="font-bold text-white/50 text-[13px] tracking-tight group-hover:text-bloom-accent transition-colors {$selectedSymbol === item.symbol ? '!text-white/80' : ''}">{item.symbol}</div>
            <div class="text-[9px] font-medium text-white/20 uppercase tracking-wide truncate pr-1">{item.name || 'Equity'}</div>
        </div>
        <div class="w-[26%] text-right pr-2">
            <div class="text-[13px] font-mono font-bold text-white/50 leading-tight {$selectedSymbol === item.symbol ? '!text-white/70' : ''}">
                {ccy}{(item.last_price ?? 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
            </div>
            <div class="text-[12px] font-bold flex items-center justify-end gap-1 {(item.daily_change_pct ?? 0) >= 0 ? 'text-green-500/70' : 'text-red-500/70'}">
                <span>{(item.daily_change_pct ?? 0) >= 0 ? '▲' : '▼'}</span>
                <span>{Math.abs(item.daily_change_pct ?? 0).toFixed(2)}%</span>
            </div>
        </div>
        <div class="w-[28%] text-right font-mono text-[12px] leading-tight space-y-0.5">
            <div class="flex justify-end gap-1.5 text-white/20">
                <span class="font-bold">H</span>
                <span class="text-white/40 font-bold">{(item.high ?? 0).toFixed(2)}</span>
            </div>
            <div class="flex justify-end gap-1.5 text-white/20">
                <span class="font-bold">L</span>
                <span class="text-white/40 font-bold">{(item.low ?? 0).toFixed(2)}</span>
            </div>
        </div>
        <div class="w-[18%] text-right">
            <div class="text-[9px] font-bold text-white/15 uppercase tracking-tighter">Vol</div>
            <div class="text-[12px] font-bold text-white/35">{formatVol(item.volume)}</div>
        </div>
    </button>
{/snippet}

<style>
    .custom-scrollbar::-webkit-scrollbar { width: 8px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: rgba(255,255,255,0.03); border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(168,85,247,0.35); border-radius: 10px; min-height: 40px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(168,85,247,0.55); }
</style>