<!--
  RankingPanel Component
  ======================
  Displays the top 3 and bottom 3 performing stocks for the selected
  index and time period, as horizontal bar charts.

  Data flow:
    - Receives `currentPeriod` as a prop from the parent page (e.g. '1y', '3mo')
    - Reads `marketIndex` from the global store (e.g. 'sp500', 'stoxx50')
    - Fetches rankings from the backend whenever either value changes
    - Caches results in memory so switching back to a previous period is instant

  The bar widths are normalized relative to the best/worst performer
  in each section, so the top gainer always fills ~75% of the row.
-->

<script>
    import { browser } from '$app/environment';
    import { API_BASE_URL } from '$lib/config.js';
    import { marketIndex } from '$lib/stores.js';

    // --- PROPS ---
    let { currentPeriod = '1y' } = $props();

    // --- LOCAL STATE ---
    let localRankings = $state(null);
    let localLoading = $state(false);
    let currentIndex = $derived($marketIndex);

    // In-memory cache: { "sp500_1y": {...}, "stoxx50_3mo": {...} }
    // Prevents redundant API calls when toggling between periods
    let rankingCache = {};

    // Tracks the in-flight request so we can cancel it if the user
    // clicks a new period before the previous one finishes
    let abortController = null;

    // Human-readable labels for the index badge
    const INDEX_LABELS = {
        sp500: 'S&P 500',
        stoxx50: 'EURO STOXX 50',
    };

    // --- DATA FETCHING ---
    async function load(period, index) {
        if (!browser) return;

        // Check cache first — instant if we've already fetched this combo
        const cacheKey = `${index}_${period}`;
        if (rankingCache[cacheKey]) {
            localRankings = rankingCache[cacheKey];
            return;
        }

        // Cancel any in-flight request to prevent stale data overwriting fresh data
        if (abortController) abortController.abort();
        abortController = new AbortController();
        const signal = abortController.signal;

        localLoading = true;
        try {
            const res = await fetch(
                `${API_BASE_URL}/rankings?period=${period}&index=${index}&t=${Date.now()}`,
                { signal },
            );
            if (!res.ok) throw new Error("API Error");
            const data = await res.json();
            localRankings = data;
            rankingCache[cacheKey] = data;
        } catch (err) {
            // AbortError means *we* cancelled it on purpose — not a real error
            if (err.name !== 'AbortError') {
                console.error("Ranking Load Error:", err);
                localRankings = null;
            }
        } finally {
            localLoading = false;
        }
    }

    // Re-fetch whenever the period or index changes
    $effect(() => {
        load(currentPeriod, currentIndex);
    });

    // --- BAR SCALING ---
    // Finds the largest absolute value in a list so we can scale bars proportionally.
    // Example: if the top gainer is +50%, its bar fills 75% of the row width.
    function getSubsetMax(items) {
        if (!items || items.length === 0) return 1;
        return Math.max(...items.map(i => Math.abs(i.value || 0)), 1);
    }
</script>

<div class="h-full w-full flex flex-col bg-white/5 rounded-3xl p-5 border border-white/5 overflow-hidden shadow-2xl backdrop-blur-md">

    <!-- Header: period label + index badge -->
    <div class="flex flex-col items-start mb-4 border-b border-white/5 pb-3">
        <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">
            Period Performance ({currentPeriod.toUpperCase()})
        </h3>
        <span class="text-[11px] font-black text-bloom-accent uppercase tracking-wider mt-1">
            {INDEX_LABELS[currentIndex] || currentIndex}
        </span>
    </div>

    <div class="flex-1 flex flex-col min-h-0 gap-1 overflow-hidden">
        {#if localRankings && localRankings.selected}
            {@const data = localRankings.selected}
            {@const topMax = getSubsetMax(data.top)}
            {@const botMax = getSubsetMax(data.bottom)}

            <div class="flex-1 flex flex-col min-h-0 gap-1">

                <!-- TOP PERFORMERS: green bars growing left-to-right -->
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

                <!-- Divider -->
                <div class="flex-none h-px bg-white/10 mx-2"></div>

                <!-- BOTTOM PERFORMERS: red bars growing right-to-left -->
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
            <!-- Loading spinner while waiting for data -->
            <div class="flex-1 flex items-center justify-center">
                <div class="w-4 h-4 border border-white/10 border-t-white/40 rounded-full animate-spin"></div>
            </div>
        {/if}
    </div>
</div>

<style>
    div { user-select: none; }

    /* Smooth animation for the bars growing/shrinking on data change */
    .transition-all {
        transition-property: all;
        transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
</style>