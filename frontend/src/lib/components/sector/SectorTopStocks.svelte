<!--
  ═══════════════════════════════════════════════════════════════════════════
   SectorTopStocks — Top / Bottom 5 Stocks in Selected Sector
  ═══════════════════════════════════════════════════════════════════════════
   Shows the best and worst performing stocks within the currently selected
   sector, across all loaded indices.  Uses a fast path from preloaded
   topStocksCache (standard periods) or falls back to the API for custom
   date ranges.  Clicking a stock selects it in the sidebar and switches
   the active index if needed.

   Data source : topStocksCache prop (preloaded via /sector-comparison/all-top-stocks)
                 GET /sector-comparison/top-stocks (fallback for custom ranges)
   Placement   : sidebar "Sector Rotation" → right column
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { browser } from '$app/environment';
    import { API_BASE_URL } from '$lib/config.js';
    import { sectorSelectedIndices, selectedSector, marketIndex, selectedSymbol, summaryData, loadSummaryData } from '$lib/stores.js';
    import { requestFocusSymbol } from '$lib/stores.js';
    import { getSectorColor } from '$lib/theme.js';
    import { INDEX_CONFIG, INDEX_COLORS } from '$lib/index-registry.js';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { fmtDate } from '$lib/format.js';

    let { currentPeriod = '1y', customRange = null, industryFilter = null, topStocksCache = null } = $props();

    // API-fetched data (fallback for custom ranges or when cache not available)
    let apiData    = $state(null);
    let loading = $state(false);
    let hasEverLoaded = $state(false);
    let apiCache   = {};

    let indices   = $derived($sectorSelectedIndices);
    let sector    = $derived($selectedSector);

    // derive display data: prefer preloaded cache, fall back to API-fetched data
    let filteredData = $derived((() => {
        const sec = sector;
        const idxList = indices;
        const filter = industryFilter;
        const isCustom = !!(customRange && customRange.start);

        // fast path: combine from preloaded cache (standard periods only)
        if (!isCustom && topStocksCache && sec && idxList && idxList.length > 0) {
            const stocks = [];
            for (const idx of idxList) {
                const idxStocks = topStocksCache[idx];
                if (!idxStocks) continue;
                for (const s of idxStocks) {
                    if (s.sector === sec) {
                        stocks.push({ ...s, index_key: idx });
                    }
                }
            }
            // apply industry filter
            const filtered = filter && filter.length > 0
                ? stocks.filter(s => new Set(filter).has(s.industry))
                : stocks;
            filtered.sort((a, b) => b.return_pct - a.return_pct);
            const n = 5;
            if (filtered.length === 0) return { top: [], bottom: [] };
            // prevent overlap: split at midpoint when fewer than 2n stocks
            const half = Math.ceil(filtered.length / 2);
            const topN = Math.min(n, half);
            const botN = Math.min(n, filtered.length - topN);
            return { top: filtered.slice(0, topN), bottom: botN > 0 ? filtered.slice(-botN).reverse() : [] };
        }

        // slow path: use API-fetched data
        if (!apiData) return null;
        if (!filter || filter.length === 0) return apiData;
        const allowed = new Set(filter);
        const all = [...(apiData.top || []), ...(apiData.bottom || [])].filter(s => allowed.has(s.industry));
        all.sort((a, b) => b.return_pct - a.return_pct);
        const n = 5;
        if (all.length === 0) return { top: [], bottom: [] };
        const half = Math.ceil(all.length / 2);
        const topN = Math.min(n, half);
        const botN = Math.min(n, all.length - topN);
        return { top: all.slice(0, topN), bottom: botN > 0 ? all.slice(-botN).reverse() : [] };
    })());

    // --- INDEX DISPLAY CONFIG ---

    // Use INDEX_CONFIG[key].abbr from stores.js

    // --- PERIOD LABEL ---

    let isCustom = $derived(!!(customRange && customRange.start));

    // --- DATA LOADING (fallback for custom ranges) ---

    async function load(sec, period, range, idxList) {
        if (!browser || !sec || !idxList || idxList.length === 0) return;
        // skip API call when preloaded cache covers this request
        if (!range?.start && topStocksCache) return;
        const idxStr = idxList.join(',');
        let url, cacheKey;
        if (range && range.start && range.end) {
            cacheKey = `topstocks_${sec}_${idxStr}_${range.start}_${range.end}`;
            url = `${API_BASE_URL}/sector-comparison/top-stocks?sector=${encodeURIComponent(sec)}&indices=${idxStr}&start=${range.start}&end=${range.end}&n=20`;
        } else {
            const p = period || '1y';
            cacheKey = `topstocks_${sec}_${idxStr}_${p}`;
            url = `${API_BASE_URL}/sector-comparison/top-stocks?sector=${encodeURIComponent(sec)}&indices=${idxStr}&period=${p}&n=20`;
        }
        if (apiCache[cacheKey]) { apiData = apiCache[cacheKey]; return; }
        loading = true;
        try {
            const ctrl = new AbortController();
            const t    = setTimeout(() => ctrl.abort(), 12000);
            const res  = await fetch(url, { signal: ctrl.signal });
            clearTimeout(t);
            if (res.ok) { const d = await res.json(); apiCache[cacheKey] = d; apiData = d; }
        } catch {}
        loading = false;
    }

    $effect(() => { load(sector, currentPeriod, customRange, indices); });

    function getSubsetMax(items) {
        if (!items || items.length === 0) return 1;
        return Math.max(...items.map(i => Math.abs(i.return_pct || 0)), 1);
    }

    // switch to the stock's parent index and focus it in the sidebar
    function navigateToStock(symbol, indexKey) {
        marketIndex.set(indexKey);
        loadSummaryData(indexKey);
        selectedSymbol.set(symbol);
        requestFocusSymbol(symbol);
    }

    let periodLabel = $derived(
        isCustom
            ? `${fmtDate(customRange.start)} → ${fmtDate(customRange.end)}`
            : (currentPeriod || '1y').toUpperCase()
    );
</script>

<Card fill class="top-stocks-root overflow-x-hidden">

    <!-- header -->
    <SectionHeader title="Top Stocks" subtitle="Stock Return %" border>
        {#snippet tooltip()}
            <div class="tt-title">Sector Stock Rankings</div>
            <div class="tt-desc">Best and worst performing stocks within the selected sector, across all loaded indices.</div>
            <div class="tt-row"><span class="tt-label">Top 5</span><span class="tt-meaning">Strongest positive returns in the sector over the period</span></div>
            <div class="tt-row"><span class="tt-label">Bot 5</span><span class="tt-meaning">Largest negative returns (losers) in the sector</span></div>
            <div class="tt-row"><span class="tt-label">Click</span><span class="tt-meaning">Select a stock to view its chart and technicals</span></div>
        {/snippet}
        {#snippet action()}
            {#if isCustom}
                <span class="text-[10px] font-semibold text-accent uppercase tracking-wider">
                    {fmtDate(customRange.start)} → {fmtDate(customRange.end)}
                </span>
            {:else if currentPeriod}
                <span class="text-[10px] font-semibold text-accent uppercase tracking-wider">
                    {currentPeriod.toUpperCase()}
                </span>
            {/if}
        {/snippet}
    </SectionHeader>
    {#if sector}
        <div class="mt-1">
            <span class="text-[13px] font-semibold uppercase tracking-wider" style="color: {sector ? getSectorColor(sector) : 'var(--text-secondary)'}">{sector}</span>
        </div>
    {/if}

    <div class="flex-1 flex flex-col min-h-0 gap-1 overflow-y-auto overflow-x-hidden">
        {#if !filteredData && (loading || !topStocksCache)}
            <div class="flex-1 flex items-center justify-center">
                <div class="w-4 h-4 border border-border border-t-text-muted rounded-full animate-spin"></div>
            </div>
        {:else if !sector}
            <div class="flex-1 flex items-center justify-center">
                <span class="text-[12px] text-text-muted font-medium uppercase tracking-widest text-center">
                    Select a sector
                </span>
            </div>
        {:else if filteredData}
            {@const topMax = getSubsetMax(filteredData.top)}
            {@const botMax = getSubsetMax(filteredData.bottom)}

            <div class="flex-1 flex flex-col min-h-0 gap-1">
                <!-- top performers -->
                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (filteredData.top || []).slice(0, 3) as item}
                        {@const width = (Math.abs(item.return_pct) / topMax) * 80}
                        {@const idxColor = INDEX_COLORS[item.index_key] || '#8b5cf6'}
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <button
                                onclick={() => navigateToStock(item.symbol, item.index_key)}
                                title="{item.name || item.symbol}"
                                class="stock-btn group/stock w-20 shrink-0 text-left cursor-pointer"
                            >
                                <div class="text-[14px] font-semibold text-text group-hover/stock:!text-text uppercase tracking-tighter truncate leading-tight transition-colors">{item.symbol}</div>
                                <div class="text-[12px] font-medium uppercase tracking-wider"
                                     style="color:{idxColor};opacity:0.85">{INDEX_CONFIG[item.index_key]?.abbr||item.index_key}</div>
                            </button>
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative">
                                <div class="h-full bg-up/15 border-l-2 border-up flex items-center justify-end rounded-sm relative transition-all duration-700 ease-out" style="width:{width}%">
                                    <span class="text-[length:var(--text-num-sm)] tabular-nums font-medium text-text whitespace-nowrap px-2 {width < 45 ? 'absolute left-full ml-1' : ''}">
                                        +{item.return_pct.toFixed(1)}%
                                    </span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>

                <div class="flex-none h-px bg-border mx-2"></div>

                <!-- bottom performers -->
                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (filteredData.bottom || []).slice(0, 3) as item}
                        {@const width = (Math.abs(item.return_pct) / botMax) * 80}
                        {@const idxColor = INDEX_COLORS[item.index_key] || '#8b5cf6'}
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <button
                                onclick={() => navigateToStock(item.symbol, item.index_key)}
                                title="{item.name || item.symbol}"
                                class="stock-btn group/stock w-20 shrink-0 text-left cursor-pointer"
                            >
                                <div class="text-[14px] font-semibold text-text group-hover/stock:!text-text uppercase tracking-tighter truncate leading-tight transition-colors">{item.symbol}</div>
                                <div class="text-[12px] font-medium uppercase tracking-wider"
                                     style="color:{idxColor};opacity:0.85">{INDEX_CONFIG[item.index_key]?.abbr||item.index_key}</div>
                            </button>
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative flex justify-end">
                                <div class="h-full bg-down/8 border-r-2 border-down flex items-center justify-start rounded-sm relative transition-all duration-700 ease-out" style="width:{width}%">
                                    <span class="text-[length:var(--text-num-sm)] tabular-nums font-medium text-text whitespace-nowrap px-2 {width < 45 ? 'absolute right-full mr-1' : ''}">
                                        {item.return_pct.toFixed(1)}%
                                    </span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        {:else}
            <div class="flex-1 flex items-center justify-center">
                <span class="text-[12px] text-text-muted font-medium uppercase tracking-widest">No data</span>
            </div>
        {/if}
    </div>
</Card>

<style>
    :global(.top-stocks-root) { container-type: inline-size; }
    div { user-select: none; }

    /* Container query: adapt stock button width to container, not viewport */
    @container (max-width: 350px) {
        .stock-btn { width: 3.5rem !important; }
    }

    @container (max-width: 280px) {
        .stock-btn { width: 3rem !important; }
    }

    /* Viewport fallback for browsers without container query support */
    @media (max-width: 640px) {
        .stock-btn { width: 3.5rem !important; }
    }
</style>
