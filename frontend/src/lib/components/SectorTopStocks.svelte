<!-- top/bottom performing stocks within selected sector across indices -->

<script>
    import { browser } from '$app/environment';
    import { API_BASE_URL } from '$lib/config.js';
    import { sectorSelectedIndices, selectedSector, marketIndex, selectedSymbol, summaryData } from '$lib/stores.js';
    import { requestFocusSymbol } from '$lib/stores.js';

    let { currentPeriod = '1y', customRange = null, industryFilter = null, topStocksCache = null } = $props();

    // API-fetched data (fallback for custom ranges or when cache not available)
    let apiData    = $state(null);
    let loading = $state(false);
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

    const INDEX_COLORS = {
        sp500:     '#e2e8f0',
        stoxx50:   '#2563eb',
        ftse100:   '#ec4899',
        nikkei225: '#f59e0b',
        csi300:    '#ef4444',
        nifty50:   '#22c55e',
    };
    const INDEX_SHORT = {
        sp500:     'S&P',
        stoxx50:   'STOXX',
        ftse100:   'FTSE',
        nikkei225: 'NIK',
        csi300:    'CSI',
        nifty50:   'NIFTY',
    };

    // --- PERIOD LABEL ---

    function fmtDate(d) {
        if (!d) return '';
        const dt = new Date(d + 'T00:00:00');
        return `${dt.getDate()} ${dt.toLocaleDateString('en-GB',{month:'short'})} '${String(dt.getFullYear()).slice(2)}`;
    }
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
        selectedSymbol.set(symbol);
        requestFocusSymbol(symbol);
    }

    let periodLabel = $derived(
        isCustom
            ? `${fmtDate(customRange.start)} → ${fmtDate(customRange.end)}`
            : (currentPeriod || '1y').toUpperCase()
    );
</script>

<div class="h-full w-full flex flex-col bg-white/5 rounded-3xl p-5 border border-white/5 overflow-x-hidden shadow-2xl backdrop-blur-md">

    <!-- header -->
    <div class="flex flex-col items-start mb-4 border-b border-white/5 pb-3">
        <div class="flex items-center gap-2">
            <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">
                Top Stocks
            </h3>
            {#if isCustom}
                <span class="text-[9px] font-black text-orange-400 uppercase tracking-wider">
                    {fmtDate(customRange.start)} → {fmtDate(customRange.end)}
                </span>
            {:else if currentPeriod}
                <span class="text-[9px] font-black text-orange-400 uppercase tracking-wider">
                    {currentPeriod.toUpperCase()}
                </span>
            {/if}
        </div>
        <div class="flex items-center gap-1.5 mt-1">
            <span class="text-[11px] font-black text-bloom-accent uppercase tracking-wider">{sector || '—'}</span>
            <span class="text-[11px] text-white/15">·</span>
            <span class="text-[11px] font-bold text-white/20 uppercase tracking-wider">Stock Return %</span>
        </div>
    </div>

    <div class="flex-1 flex flex-col min-h-0 gap-1 overflow-y-auto overflow-x-hidden">
        {#if !filteredData && loading}
            <div class="flex-1 flex items-center justify-center">
                <div class="w-4 h-4 border border-white/10 border-t-white/40 rounded-full animate-spin"></div>
            </div>
        {:else if !sector}
            <div class="flex-1 flex items-center justify-center">
                <span class="text-[11px] text-white/15 font-bold uppercase tracking-widest text-center">
                    Select a sector
                </span>
            </div>
        {:else if filteredData}
            {@const topMax = getSubsetMax(filteredData.top)}
            {@const botMax = getSubsetMax(filteredData.bottom)}

            <div class="flex-1 flex flex-col min-h-0 gap-1">
                <!-- top performers -->
                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (filteredData.top || []).slice(0, 5) as item}
                        {@const width = (Math.abs(item.return_pct) / topMax) * 80}
                        {@const idxColor = INDEX_COLORS[item.index_key] || '#8b5cf6'}
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <button
                                onclick={() => navigateToStock(item.symbol, item.index_key)}
                                title="{item.name || item.symbol}"
                                class="group/stock w-20 shrink-0 text-left cursor-pointer"
                            >
                                <div class="text-[13px] font-black text-white/80 group-hover/stock:!text-bloom-accent uppercase tracking-tighter truncate leading-tight transition-colors">{item.symbol}</div>
                                <div class="text-[11px] font-bold uppercase tracking-wider"
                                     style="color:{idxColor};opacity:0.85">{INDEX_SHORT[item.index_key]||item.index_key}</div>
                            </button>
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative">
                                <div class="h-full bg-green-500/20 border-l-2 border-green-500 flex items-center justify-end rounded-sm relative transition-all duration-700 ease-out" style="width:{width}%">
                                    <span class="text-[11px] font-medium text-white/80 whitespace-nowrap px-2 {width < 45 ? 'absolute left-full ml-1' : ''}">
                                        +{item.return_pct.toFixed(1)}%
                                    </span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>

                <div class="flex-none h-px bg-white/10 mx-2"></div>

                <!-- bottom performers -->
                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (filteredData.bottom || []).slice(0, 5) as item}
                        {@const width = (Math.abs(item.return_pct) / botMax) * 80}
                        {@const idxColor = INDEX_COLORS[item.index_key] || '#8b5cf6'}
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <button
                                onclick={() => navigateToStock(item.symbol, item.index_key)}
                                title="{item.name || item.symbol}"
                                class="group/stock w-20 shrink-0 text-left cursor-pointer"
                            >
                                <div class="text-[13px] font-black text-white/80 group-hover/stock:!text-bloom-accent uppercase tracking-tighter truncate leading-tight transition-colors">{item.symbol}</div>
                                <div class="text-[11px] font-bold uppercase tracking-wider"
                                     style="color:{idxColor};opacity:0.85">{INDEX_SHORT[item.index_key]||item.index_key}</div>
                            </button>
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative flex justify-end">
                                <div class="h-full bg-red-500/20 border-r-2 border-red-500 flex items-center justify-start rounded-sm relative transition-all duration-700 ease-out" style="width:{width}%">
                                    <span class="text-[11px] font-medium text-white/80 whitespace-nowrap px-2 {width < 45 ? 'absolute right-full mr-1' : ''}">
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
                <span class="text-[11px] text-white/15 font-bold uppercase tracking-widest">No data</span>
            </div>
        {/if}
    </div>
</div>

<style>
    div { user-select: none; }
</style>
