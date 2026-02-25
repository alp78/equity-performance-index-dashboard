<!--
  ═══════════════════════════════════════════════════════════════════════════
   StockBrowser — Stock Browsing Mode Panel
  ═══════════════════════════════════════════════════════════════════════════
   Extracted from Sidebar.svelte.  Handles the stock browsing mode:

   - Index dropdown selector (6 indices grouped West / East)
   - Full-text search across symbols and names
   - Collapsible sector > industry > stock hierarchy
   - Session-persisted open sectors and per-index symbol history
   - Fresh-session auto-select picks the most active stock by volume
   - "Sector Analysis" hover button on each sector header

   Props:
     onGoToSectorAnalysis(sectorName) — callback to switch to sector mode

   All market state comes from $lib/stores.js reactive stores.
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { onMount, tick } from 'svelte';
    import { browser } from '$app/environment';
    import {
        selectedSymbol,
        summaryData,
        loadSummaryData,
        marketIndex,
        currentCurrency,
        INDEX_CONFIG,
        INDEX_GROUPS,
        focusSymbolRequest,
        isOverviewMode,
        isSectorMode,
    } from '$lib/stores.js';
    import { getSectorColor } from '$lib/theme.js';

    // --- PROPS ---

    let { onGoToSectorAnalysis } = $props();

    // --- UI STATE ---

    let searchQuery = $state('');
    let debouncedQuery = $state('');
    let _debounceTimer;
    let dropdownOpen = $state(false);
    let scrollContainer;
    let openSectors = $state((() => {
        if (!browser) return new Set();
        try {
            const saved = sessionStorage.getItem('dash_open_sectors');
            return saved ? new Set(JSON.parse(saved)) : new Set();
        } catch { return new Set(); }
    })());

    // persist open sectors to sessionStorage
    $effect(() => {
        if (!browser) return;
        try { sessionStorage.setItem('dash_open_sectors', JSON.stringify([...openSectors])); } catch {}
    });

    // debounce searchQuery -> debouncedQuery (300ms) to avoid O(n log n) sectorTree recomputation on every keystroke
    $effect(() => {
        const q = searchQuery;
        if (_debounceTimer) clearTimeout(_debounceTimer);
        if (!q) {
            // clear immediately for instant reset
            debouncedQuery = '';
        } else {
            _debounceTimer = setTimeout(() => { debouncedQuery = q; }, 300);
        }
        return () => { if (_debounceTimer) clearTimeout(_debounceTimer); };
    });

    // --- DERIVED STATE ---

    let tickers = $derived($summaryData.assets || []);
    let loading = $derived($summaryData.loading);
    let error = $derived($summaryData.error);
    let currentIndex = $derived($marketIndex);
    let ccy = $derived($currentCurrency);
    let currentConfig = $derived(INDEX_CONFIG[currentIndex]);

    // --- SELECTED STOCK'S SECTOR ---
    let selectedStockSector = $derived((() => {
        const sym = $selectedSymbol;
        if (!sym) return null;
        const t = tickers.find(t => t.symbol === sym);
        return t ? ((t.sector && t.sector !== 0) ? t.sector : 'Other') : null;
    })());

    // --- SECTOR TREE ---

    // build sector > industry > stocks hierarchy, filtered by debounced search query
    let sectorTree = $derived((() => {
        const q = debouncedQuery.toLowerCase();
        const filtered = tickers.filter(t =>
            !debouncedQuery ||
            t.symbol.toLowerCase().includes(q) ||
            (t.name && t.name.toLowerCase().includes(q))
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

    // --- SECTOR EXPAND / COLLAPSE HELPERS ---

    function openSectorFor(symbol) {
        const asset = tickers.find(t => t.symbol === symbol);
        if (!asset) return;
        const sectorName = (asset.sector && asset.sector !== 0) ? asset.sector : 'Other';
        if (!openSectors.has(sectorName)) {
            openSectors = new Set([...openSectors, sectorName]);
        }
    }

    // collapse all sectors then open only the one containing this symbol
    function resetSectorsTo(symbol, tickerList) {
        const asset = tickerList.find(t => t.symbol === symbol);
        if (!asset) { openSectors = new Set(); return; }
        const sectorName = (asset.sector && asset.sector !== 0) ? asset.sector : 'Other';
        openSectors = new Set([sectorName]);
    }

    function toggleSector(sector) {
        const next = new Set(openSectors);
        next.has(sector) ? next.delete(sector) : next.add(sector);
        openSectors = next;
    }

    function selectTicker(symbol) {
        selectedSymbol.set(symbol);
        lastSymbolPerIndex[$marketIndex] = symbol;
        openSectorFor(symbol);
    }

    // --- FOCUS REQUEST HANDLER ---

    // handle focus requests from TopMovers/MostActive: open sector + scroll into view.
    // uses a seq counter so re-clicking the same symbol still triggers.
    let lastFocusSeq = 0;
    $effect(() => {
        const req = $focusSymbolRequest;
        if (!req.symbol || req.seq === lastFocusSeq) return;
        lastFocusSeq = req.seq;

        if (tickers.length === 0) return;
        resetSectorsTo(req.symbol, tickers);
        selectedSymbol.set(req.symbol);

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

    // --- INDEX SWITCHING ---

    // remember the last selected symbol per index so switching back restores it
    let lastSymbolPerIndex = Object.fromEntries(
        Object.entries(INDEX_CONFIG).map(([key, cfg]) => [key, cfg.defaultSymbol])
    );

    // track which indices have had their first stock-mode visit this session
    // first visit auto-selects the most active stock by volume
    let visitedIndices = new Set();

    // pick the most actively traded stock (highest volume) from loaded data
    function topVolumeSymbol(assets) {
        if (!assets || !assets.length) return null;
        return [...assets]
            .filter(a => a.volume > 0 && a.last_price > 0)
            .sort((a, b) => (b.volume || 0) - (a.volume || 0))[0]?.symbol || null;
    }

    async function switchIndex(key) {
        if (key === currentIndex) return;
        lastSymbolPerIndex[currentIndex] = $selectedSymbol;
        marketIndex.set(key);
        searchQuery = '';
        debouncedQuery = '';
        dropdownOpen = false;
        const newTickers = await loadSummaryData(key);
        // first visit to this index -> auto-select most active stock by volume
        let newSymbol;
        if (!visitedIndices.has(key) && newTickers && newTickers.length > 0) {
            newSymbol = topVolumeSymbol(newTickers) || INDEX_CONFIG[key]?.defaultSymbol || '';
            visitedIndices.add(key);
        } else {
            newSymbol = lastSymbolPerIndex[key] || INDEX_CONFIG[key]?.defaultSymbol || '';
        }
        lastSymbolPerIndex[key] = newSymbol;
        selectedSymbol.set(newSymbol);
        if (newTickers && newTickers.length > 0) {
            resetSectorsTo(newSymbol, newTickers);
        }
        // scroll to selected stock after DOM updates
        tick().then(() => {
            if (!scrollContainer) return;
            const el = scrollContainer.querySelector(`[data-symbol="${CSS.escape(newSymbol)}"]`);
            if (el) {
                const cr = scrollContainer.getBoundingClientRect();
                const er = el.getBoundingClientRect();
                scrollContainer.scrollTop += er.top - cr.top - (cr.height / 2) + (er.height / 2);
            }
        });
    }

    // --- LIFECYCLE ---

    // detect fresh session (no user-chosen symbol persisted)
    const _freshSession = browser && !sessionStorage.getItem('dash_symbol');

    // scroll sidebar to center the selected stock after DOM updates
    function scrollToSelectedStock(symbol) {
        if (!scrollContainer || !symbol) return;
        tick().then(() => {
            const el = scrollContainer.querySelector(`[data-symbol="${CSS.escape(symbol)}"]`);
            if (el) {
                const cr = scrollContainer.getBoundingClientRect();
                const er = el.getBoundingClientRect();
                scrollContainer.scrollTop += er.top - cr.top - (cr.height / 2) + (er.height / 2);
            }
        });
    }

    // Track whether this mount has successfully expanded+scrolled
    let _mountReady = false;

    function expandAndScroll(assets) {
        if (_mountReady) return;
        const sym = $selectedSymbol;
        const idx = $marketIndex;
        if (!idx || !INDEX_CONFIG[idx] || !assets || assets.length === 0) return;
        // Only mark ready if the selected symbol exists in the data
        const found = assets.find(t => t.symbol === sym);
        if (!found) return;
        _mountReady = true;
        visitedIndices.add(idx);
        resetSectorsTo(sym, assets);
        scrollToSelectedStock(sym);
    }

    // Reactive: when tickers arrive (or change), expand the selected stock's
    // sector and scroll to center it. Runs once per mount thanks to _mountReady.
    $effect(() => {
        if (_mountReady) return;
        if (tickers.length === 0) return;
        const idx = currentIndex;
        if (!idx || !INDEX_CONFIG[idx]) return;
        expandAndScroll(tickers);
    });

    onMount(async () => {
        document.addEventListener('click', handleClickOutside);

        if (!$summaryData.loaded && !$summaryData.loading) {
            const idx = $marketIndex;
            // only load if we are in a real stock index (not macro/sectors)
            if (idx && INDEX_CONFIG[idx]) {
                const data = await loadSummaryData(idx);
                if (data && data.length > 0) {
                    // fresh session + first index load -> auto-select most active stock by volume
                    if (_freshSession && !visitedIndices.has(idx)) {
                        const top = topVolumeSymbol(data);
                        if (top) {
                            selectedSymbol.set(top);
                            lastSymbolPerIndex[idx] = top;
                        }
                    }
                    expandAndScroll(data);
                }
            }
        } else if ($summaryData.assets.length > 0) {
            expandAndScroll($summaryData.assets);
        }

        return () => document.removeEventListener('click', handleClickOutside);
    });

    // --- CLICK OUTSIDE HANDLER ---

    function handleClickOutside(e) {
        if (dropdownOpen && !e.target.closest('.index-dropdown')) dropdownOpen = false;
    }

    // --- FORMATTERS ---

    function formatVol(val) {
        if (!val) return '0';
        if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
        if (val >= 1000) return (val / 1000).toFixed(0) + 'K';
        return val.toString();
    }

    // --- PUBLIC API ---

    export function openSectorForSymbol(symbol) {
        openSectorFor(symbol);
    }
</script>

<!-- index dropdown selector -->
<div class="p-4 space-y-3">
    <div class="relative index-dropdown">
        <button
            onclick={() => dropdownOpen = !dropdownOpen}
            aria-label="Select market index"
            aria-haspopup="true"
            aria-expanded={dropdownOpen}
            class="w-full flex items-center justify-between bg-bg-card border border-border rounded-lg px-4 py-2.5 hover:border-accent/40 transition-all group"
        >
            <div class="flex items-center gap-3">
                <span class="{INDEX_CONFIG[currentIndex]?.flag || ''} fis rounded-sm" style="font-size: 1.3rem;"></span>
                <span class="text-[20px] font-semibold text-text">{currentConfig?.shortLabel}</span>
                <span class="text-[18px] font-medium text-text-muted uppercase ml-1">{currentConfig?.currencyCode}</span>
                <span class="text-[18px] font-medium text-text-muted">{currentConfig?.currency}</span>
            </div>
            <svg class="w-4 h-4 text-text-faint group-hover:text-accent transition-all {dropdownOpen ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
        </button>

        {#if dropdownOpen}
            <div class="absolute top-full left-0 right-0 mt-1 bg-bg-card border border-border rounded-lg shadow-lg z-50 overflow-hidden">
                {#each INDEX_GROUPS as group}
                    <div class="px-4 py-2 text-[11px] font-semibold text-text-faint uppercase tracking-widest border-b border-border-subtle">{group.label}</div>
                    {#each group.indices as idx}
                        <button
                            onclick={() => switchIndex(idx.key)}
                            class="w-full flex items-center justify-between px-4 py-3 hover:bg-bg-hover transition-all
                            {idx.key === currentIndex ? 'bg-bg-selected border-l-[3px] border-l-text-faint' : 'border-l-[3px] border-l-transparent'}"
                        >
                            <div class="flex items-center gap-3">
                                <span class="{INDEX_CONFIG[idx.key]?.flag || ''} fis rounded-sm" style="font-size: 1.2rem;"></span>
                                <span class="text-[16px] font-semibold text-text-secondary">{idx.shortLabel}</span>
                            </div>
                            <div class="flex items-center gap-2 pr-2">
                                <span class="text-[14px] font-medium text-text-muted">{idx.currencyCode}</span>
                                <span class="text-[14px] font-medium text-text-muted">{idx.currency}</span>
                                {#if idx.key === currentIndex}
                                    <span class="ml-1 w-1.5 h-1.5 rounded-full bg-text-faint"></span>
                                {/if}
                            </div>
                        </button>
                    {/each}
                {/each}
            </div>
        {/if}
    </div>

    <!-- search bar + expand/collapse -->
    <div class="flex gap-2">
        <div class="relative group flex-1">
            <input type="text" bind:value={searchQuery} placeholder="Search symbol or name..." aria-label="Search stocks by symbol or name"
                class="w-full bg-bg-card border border-border rounded-lg py-2 px-4 pl-10 text-sm text-text placeholder:text-text-faint focus:outline-none focus:border-text-faint focus:ring-2 focus:ring-text-faint/10 transition-all"
            />
            <svg class="absolute left-3 top-2.5 w-4 h-4 text-text-faint group-focus-within:text-text-secondary transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
        </div>
        <button onclick={() => { openSectors = new Set(sectorTree.map(s => s.name)); }} title="Expand all" aria-label="Expand all sectors"
            class="shrink-0 w-9 flex items-center justify-center bg-bg-card border border-border rounded-lg hover:bg-bg-hover transition-all text-text-faint hover:text-accent"
        >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
        </button>
        <button onclick={() => { openSectors = new Set(); }} title="Collapse all" aria-label="Collapse all sectors"
            class="shrink-0 w-9 flex items-center justify-center bg-bg-card border border-border rounded-lg hover:bg-bg-hover transition-all text-text-faint hover:text-accent"
        >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
            </svg>
        </button>
    </div>
</div>

<!-- scrollable stock list -->
<div bind:this={scrollContainer} class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar">
    {#if loading}
        <div class="flex flex-col items-center justify-center h-40 space-y-3">
            <div class="w-5 h-5 border-2 border-border border-t-text-muted rounded-full animate-spin"></div>
            <span class="text-[12px] font-semibold uppercase tracking-widest text-text-muted">Loading</span>
        </div>
    {:else if error}
        <div class="flex flex-col items-center justify-center h-40 space-y-3 px-6">
            <div class="text-center space-y-2">
                <div class="text-down text-sm font-medium">Failed to load market data</div>
                <div class="text-text-faint text-xs font-mono">{error}</div>
            </div>
            <button onclick={() => loadSummaryData(currentIndex)} class="px-4 py-2 bg-bg-hover hover:bg-bg-active border border-border rounded-lg text-text-secondary text-xs font-medium uppercase tracking-wider transition-all">Retry</button>
        </div>
    {:else if debouncedQuery}
        <!-- flat list of matching stocks when searching -->
        {#each sectorTree.flatMap(s => s.industries.flatMap(i => i.stocks)) as item}
            {@render stockRow(item)}
        {/each}
    {:else}
        <!-- collapsible sector > industry > stock tree -->
        {#each sectorTree as sector}
            <div class="w-full flex items-center border-b border-border sticky top-0 z-10 bg-surface-2 group/sector">
                <!-- chevron: expand/collapse only -->
                <button onclick={() => toggleSector(sector.name)} title="Expand {sector.name}" aria-label="Toggle {sector.name} sector" aria-expanded={openSectors.has(sector.name)}
                    class="pl-4 py-2.5 pr-1 flex items-center hover:bg-bg-active transition-all rounded-l-sm">
                    <svg class="w-3 h-3 text-text-muted transition-transform {openSectors.has(sector.name) ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                    </svg>
                </button>
                <!-- sector name (clickable to expand/collapse) + "Sector Analysis" on hover + stock count -->
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div onclick={() => toggleSector(sector.name)}
                     onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleSector(sector.name); } }}
                     class="flex-1 flex items-center py-2.5 pr-4 pl-1.5 min-w-0 text-left hover:bg-bg-active transition-all cursor-pointer"
                     role="button" tabindex="-1">
                    <span class="text-[13px] font-bold uppercase tracking-widest truncate transition-colors"
                          style="color: {selectedStockSector === sector.name ? getSectorColor(sector.name) : 'var(--text-secondary)'}">{sector.name}</span>
                    <div class="ml-auto flex items-center gap-3 shrink-0">
                        <button onclick={(e) => { e.stopPropagation(); onGoToSectorAnalysis(sector.name); }}
                            aria-label="View sector analysis for {sector.name}"
                            class="opacity-0 group-hover/sector:opacity-100 transition-opacity text-[10px] font-semibold text-text-muted uppercase tracking-wider hover:text-text cursor-pointer whitespace-nowrap">
                            Sector Analysis
                        </button>
                        <span class="text-[13px] font-medium text-text-faint tabular-nums w-6 text-right">{sector.stockCount}</span>
                    </div>
                </div>
            </div>

            {#if openSectors.has(sector.name)}
                {#each sector.industries as industry}
                    <div class="flex items-center gap-2 px-4 py-[6px] bg-surface-2">
                        <span class="text-[12px] font-semibold text-text-faint uppercase tracking-widest">{industry.name}</span>
                    </div>
                    {#each industry.stocks as item}
                        {@render stockRow(item)}
                    {/each}
                {/each}
            {/if}
        {/each}
    {/if}
</div>

{#snippet stockRow(item)}
    {@const sectorColor = getSectorColor((item.sector && item.sector !== 0) ? item.sector : 'Other')}
    <button
        data-symbol={item.symbol}
        onclick={() => selectTicker(item.symbol)}
        title="{item.symbol}{item.name ? ' — ' + item.name : ''}"
        aria-label="Select {item.symbol}{item.name ? ' (' + item.name + ')' : ''}"
        class="w-full pl-6 pr-3 py-1.5 flex items-center border-b border-border-subtle hover:bg-bg-hover transition-all relative overflow-hidden group"
        style="{$selectedSymbol === item.symbol ? `background:${sectorColor}18` : ''}"
    >
        {#if $selectedSymbol === item.symbol}
            <div class="absolute left-0 top-0 bottom-0 w-[2px]" style="background:{sectorColor}"></div>
        {/if}
        <div class="w-[28%] text-left overflow-hidden pl-1 stock-col-symbol">
            <div class="font-medium text-text-muted text-[15px] tracking-tight group-hover:text-text transition-colors">{item.symbol}</div>
        </div>
        <div class="w-[26%] text-right pr-2 stock-col-price">
            <div class="text-[13px] font-mono font-semibold text-text-secondary leading-tight">
                {ccy}{(item.last_price ?? 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
            </div>
            <div class="text-[13px] font-semibold flex items-center justify-end gap-1 {(item.daily_change_pct ?? 0) >= 0 ? 'text-up' : 'text-down'}">
                <span class="font-mono tabular-nums">{(item.daily_change_pct ?? 0) >= 0 ? '+' : ''}{(item.daily_change_pct ?? 0).toFixed(2)}%</span>
            </div>
        </div>
        <div class="w-[28%] text-right font-mono text-[13px] leading-tight space-y-0.5 stock-col-hl">
            <div class="flex justify-end gap-1.5 text-text-muted">
                <span class="font-medium">H</span>
                <span class="text-text-muted font-medium">{(item.high ?? 0).toFixed(2)}</span>
            </div>
            <div class="flex justify-end gap-1.5 text-text-muted">
                <span class="font-medium">L</span>
                <span class="text-text-muted font-medium">{(item.low ?? 0).toFixed(2)}</span>
            </div>
        </div>
        <div class="w-[18%] text-right stock-col-vol">
            <div class="text-[11px] font-medium text-text-muted uppercase tracking-tighter">Vol</div>
            <div class="text-[13px] font-mono font-medium text-text-muted">{formatVol(item.volume)}</div>
        </div>
    </button>
{/snippet}

<style>
    .custom-scrollbar::-webkit-scrollbar { width: 6px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: transparent; border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 10px; min-height: 40px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }

    @media (max-width: 1024px) {
        .stock-col-hl { display: none; }
        .stock-col-vol { display: none; }
        .stock-col-symbol { width: 50% !important; }
        .stock-col-price { width: 50% !important; }
    }
</style>
