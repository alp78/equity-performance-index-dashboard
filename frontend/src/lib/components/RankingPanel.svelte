<!--
  RankingPanel Component
  ======================
  Displays top 3 / bottom 3 performers as bar charts.
  
  Two modes:
    - Period: currentPeriod = '1y' etc → /rankings endpoint
    - Custom: customRange = { start, end } → /rankings/custom endpoint
  
  Custom range dates are shown next to "Top Movers" title.
  Index label always shown on the second line.
-->

<script>
    import { browser } from '$app/environment';
    import { API_BASE_URL } from '$lib/config.js';
    import { marketIndex, INDEX_CONFIG, selectedSymbol, summaryData } from '$lib/stores.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let localRankings = $state(null);
    let localLoading = $state(false);
    let currentIndex = $derived($marketIndex);
    let rankingCache = {};
    let abortController = null;

    // Build name map from sidebar data
    let nameMap = $derived(
        Object.fromEntries(($summaryData.assets || []).map(a => [a.symbol, a.name || '']))
    );

    function selectSymbol(symbol) {
        selectedSymbol.set(symbol);
    }

    function formatDateShort(dateStr) {
        if (!dateStr) return '';
        const d = new Date(dateStr + 'T00:00:00');
        return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
    }

    async function load(period, index, range) {
        if (!browser) return;

        let url, cacheKey;
        if (range) {
            cacheKey = `${index}_custom_${range.start}_${range.end}`;
            url = `${API_BASE_URL}/rankings/custom?start=${range.start}&end=${range.end}&index=${index}&t=${Date.now()}`;
        } else if (period) {
            cacheKey = `${index}_${period}`;
            url = `${API_BASE_URL}/rankings?period=${period}&index=${index}&t=${Date.now()}`;
        } else {
            return;
        }

        // Only use cache if it has actual data
        if (rankingCache[cacheKey]) {
            const cached = rankingCache[cacheKey];
            if (cached?.selected?.top?.length > 0 || cached?.selected?.bottom?.length > 0) {
                localRankings = cached;
                return;
            }
        }

        if (abortController) abortController.abort();
        abortController = new AbortController();

        localLoading = true;

        // Retry up to 8 times — backend may still be loading the index from BigQuery
        const maxRetries = 8;
        for (let attempt = 0; attempt < maxRetries; attempt++) {
            try {
                const res = await fetch(url, { signal: abortController.signal });
                if (!res.ok) throw new Error("API Error");
                const data = await res.json();

                // Check if we got real data
                const hasData = data?.selected?.top?.length > 0 || data?.selected?.bottom?.length > 0;
                if (hasData) {
                    localRankings = data;
                    rankingCache[cacheKey] = data;
                    localLoading = false;
                    return;
                }

                // Empty = backend still loading, retry with short delay
                if (attempt < maxRetries - 1) {
                    await new Promise(r => setTimeout(r, 1500));
                    continue;
                }

                localRankings = data;
            } catch (err) {
                if (err.name === 'AbortError') return;
                if (attempt < maxRetries - 1) {
                    await new Promise(r => setTimeout(r, 1500));
                    continue;
                }
                console.error("Ranking Load Error:", err);
                localRankings = null;
            }
        }
        localLoading = false;
    }

    let lastRankingIndex = '';

    $effect(() => {
        const idx = currentIndex;
        // Clear rankings when index changes to show loading spinner
        if (idx !== lastRankingIndex) {
            lastRankingIndex = idx;
            localRankings = null;
        }
        load(currentPeriod, idx, customRange);
    });

    function getSubsetMax(items) {
        if (!items || items.length === 0) return 1;
        return Math.max(...items.map(i => Math.abs(i.value || 0)), 1);
    }
</script>

<div class="h-full w-full flex flex-col bg-white/5 rounded-3xl p-5 border border-white/5 overflow-hidden shadow-2xl backdrop-blur-md">

    <!-- Header -->
    <div class="flex flex-col items-start mb-4 border-b border-white/5 pb-3">
        <div class="flex items-center gap-2">
            <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">
                Top Movers
            </h3>
            {#if customRange}
                <span class="text-[9px] font-black text-orange-400 uppercase tracking-wider">
                    {formatDateShort(customRange.start)} → {formatDateShort(customRange.end)}
                </span>
            {:else if currentPeriod}
                <span class="text-[9px] font-black text-orange-400 uppercase tracking-wider">
                    {currentPeriod.toUpperCase()}
                </span>
            {/if}
        </div>
        <!-- Index label always visible -->
        <span class="text-[11px] font-black text-bloom-accent uppercase tracking-wider mt-1">
            {INDEX_CONFIG[currentIndex]?.label || currentIndex}
        </span>
    </div>

    <div class="flex-1 flex flex-col min-h-0 gap-1 overflow-hidden">
        {#if localRankings && localRankings.selected}
            {@const data = localRankings.selected}
            {@const topMax = getSubsetMax(data.top)}
            {@const botMax = getSubsetMax(data.bottom)}

            <div class="flex-1 flex flex-col min-h-0 gap-1">
                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (data.top || []).slice(0, 3) as item}
                        {@const width = (Math.abs(item.value) / topMax) * 70}
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <button
                                onclick={() => selectSymbol(item.symbol)}
                                title="{item.symbol}{nameMap[item.symbol] ? ' — ' + nameMap[item.symbol] : ''}"
                                class="w-20 text-[11px] font-black text-white shrink-0 uppercase tracking-tighter truncate text-left hover:text-bloom-accent transition-colors cursor-pointer"
                            >{item.symbol}</button>
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative">
                                <div class="h-full bg-green-500/20 border-l-2 border-green-500 flex items-center justify-end rounded-sm relative transition-all duration-700 ease-out" style="width: {width}%">
                                    <span class="text-[11px] font-medium text-white whitespace-nowrap px-2 {width < 45 ? 'absolute left-full ml-1' : ''}">+{item.value.toFixed(1)}%</span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>

                <div class="flex-none h-px bg-white/10 mx-2"></div>

                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (data.bottom || []).slice(0, 3) as item}
                        {@const width = (Math.abs(item.value) / botMax) * 70}
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <button
                                onclick={() => selectSymbol(item.symbol)}
                                title="{item.symbol}{nameMap[item.symbol] ? ' — ' + nameMap[item.symbol] : ''}"
                                class="w-20 text-[11px] font-black text-white shrink-0 uppercase tracking-tighter truncate text-left hover:text-bloom-accent transition-colors cursor-pointer"
                            >{item.symbol}</button>
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative flex justify-end">
                                <div class="h-full bg-red-500/20 border-r-2 border-red-500 flex items-center justify-start rounded-sm relative transition-all duration-700 ease-out" style="width: {width}%">
                                    <span class="text-[11px] font-medium text-white whitespace-nowrap px-2 {width < 45 ? 'absolute right-full mr-1' : ''}">
                                        {item.value.toFixed(1)}%
                                    </span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        {:else}
            <div class="flex-1 flex items-center justify-center">
                <div class="w-4 h-4 border border-white/10 border-t-white/40 rounded-full animate-spin"></div>
            </div>
        {/if}
    </div>
</div>

<style>
    div { user-select: none; }
    .transition-all { transition-property: all; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }
</style>