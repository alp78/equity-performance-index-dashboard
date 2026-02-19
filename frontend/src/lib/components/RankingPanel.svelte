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
    import { marketIndex, INDEX_CONFIG } from '$lib/stores.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let localRankings = $state(null);
    let localLoading = $state(false);
    let currentIndex = $derived($marketIndex);
    let rankingCache = {};
    let abortController = null;

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

        if (rankingCache[cacheKey]) {
            localRankings = rankingCache[cacheKey];
            return;
        }

        if (abortController) abortController.abort();
        abortController = new AbortController();

        localLoading = true;
        try {
            const res = await fetch(url, { signal: abortController.signal });
            if (!res.ok) throw new Error("API Error");
            const data = await res.json();
            localRankings = data;
            rankingCache[cacheKey] = data;
        } catch (err) {
            if (err.name !== 'AbortError') {
                console.error("Ranking Load Error:", err);
                localRankings = null;
            }
        } finally {
            localLoading = false;
        }
    }

    $effect(() => {
        load(currentPeriod, currentIndex, customRange);
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
                        {@const width = (Math.abs(item.value) / topMax) * 75}
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <span class="w-12 text-[12px] font-black text-white shrink-0 uppercase tracking-tighter mr-2">{item.symbol}</span>
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
                        {@const width = (Math.abs(item.value) / botMax) * 75}
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <span class="w-12 text-[12px] font-black text-white shrink-0 uppercase tracking-tighter mr-2">{item.symbol}</span>
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