<!--
  ═══════════════════════════════════════════════════════════════════════════
   RankingPanel — Top / Bottom 3 Stock Movers
  ═══════════════════════════════════════════════════════════════════════════
   Horizontal bar chart showing the 3 best and 3 worst performing stocks
   by price return over the selected period.  Retries up to 8 times with
   1.5 s backoff since the backend may still be loading from BigQuery.
   Clicking a stock selects it in the sidebar.

   Data source : GET /rankings?period={p}&index={idx}
                 GET /rankings/custom?start=...&end=...
   Placement   : bottom panel grid, col-span-3
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { browser } from '$app/environment';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { marketIndex, INDEX_CONFIG, selectedSymbol, summaryData, requestFocusSymbol } from '$lib/stores.js';
    let { currentPeriod = '1y', customRange = null } = $props();

    let localRankings = $state(null);
    let localLoading = $state(false);
    let currentIndex = $derived($marketIndex);
    let indexFlag = $derived(INDEX_CONFIG[currentIndex]?.flag || '');
    let rankingCache = {};
    let abortController = null;

    // map symbol -> company name from sidebar data
    let nameMap = $derived(
        Object.fromEntries(($summaryData.assets || []).map(a => [a.symbol, a.name || '']))
    );

    function selectSymbol(symbol) {
        selectedSymbol.set(symbol);
        requestFocusSymbol(symbol);
    }

    function formatDateShort(dateStr) {
        if (!dateStr) return '';
        const d = new Date(dateStr + 'T00:00:00');
        const dd = String(d.getDate()).padStart(2, '0');
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const yy = String(d.getFullYear()).slice(2);
        return `${dd}/${mm}/${yy}`;
    }

    // --- DATA LOADING ---
    // retries up to 8 times since backend may still be loading from BigQuery
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
            const cached = rankingCache[cacheKey];
            if (cached?.selected?.top?.length > 0 || cached?.selected?.bottom?.length > 0) {
                localRankings = cached;
                return;
            }
        }

        if (abortController) abortController.abort();
        abortController = new AbortController();

        localLoading = true;

        const maxRetries = 8;
        for (let attempt = 0; attempt < maxRetries; attempt++) {
            try {
                const res = await fetch(url, { signal: abortController.signal });
                if (!res.ok) throw new Error("API Error");
                const data = await res.json();

                const hasData = data?.selected?.top?.length > 0 || data?.selected?.bottom?.length > 0;
                if (hasData) {
                    localRankings = data;
                    rankingCache[cacheKey] = data;
                    localLoading = false;
                    return;
                }

                // empty response = backend still loading, retry after delay
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

    // clear stale rankings on index switch so spinner shows
    $effect(() => {
        const idx = currentIndex;
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

<Card fill class="top-movers-root overflow-x-hidden">

    <!-- header -->
    <SectionHeader title="Top Movers" subtitle={INDEX_CONFIG[currentIndex]?.abbr || currentIndex} subtitleFlag={indexFlag} border size="lg">
        {#snippet action()}
            {#if customRange}
                <span class="text-[10px] font-semibold text-accent uppercase tracking-wider">
                    {formatDateShort(customRange.start)} → {formatDateShort(customRange.end)}
                </span>
            {:else if currentPeriod}
                <span class="text-[10px] font-semibold text-accent uppercase tracking-wider">
                    {currentPeriod.toUpperCase()}
                </span>
            {/if}
        {/snippet}
    </SectionHeader>

    <div class="flex-1 flex flex-col min-h-0 gap-1 overflow-y-auto overflow-x-hidden">
        {#if localRankings && localRankings.selected}
            {@const data = localRankings.selected}
            {@const topMax = getSubsetMax(data.top)}
            {@const botMax = getSubsetMax(data.bottom)}

            <div class="flex-1 flex flex-col min-h-0 gap-1">
                <!-- top performers -->
                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (data.top || []).slice(0, 3) as item}
                        {@const width = (Math.abs(item.value) / topMax) * 80}
                        <div class="bar-row flex items-center w-full gap-2 flex-1 min-h-0">
                            <button
                                onclick={() => selectSymbol(item.symbol)}
                                title="{item.symbol}{nameMap[item.symbol] ? ' — ' + nameMap[item.symbol] : ''}"
                                aria-label="Select {item.symbol}{nameMap[item.symbol] ? ' - ' + nameMap[item.symbol] : ''}"
                                class="w-20 text-[14px] font-semibold text-text shrink-0 uppercase tracking-tighter truncate text-left hover:text-text transition-colors cursor-pointer"
                            >{item.symbol}</button>
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative">
                                <div class="h-full bg-up/15 border-l-2 border-up flex items-center justify-end rounded-sm relative transition-all duration-700 ease-out" style="width: {width}%">
                                    <span class="text-[12px] font-mono tabular-nums font-medium text-text whitespace-nowrap px-2 {width < 45 ? 'absolute left-full ml-1' : ''}">+{(item.value ?? 0).toFixed(1)}%</span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>

                <div class="flex-none h-px bg-border mx-2"></div>

                <!-- bottom performers -->
                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (data.bottom || []).slice(0, 3) as item}
                        {@const width = (Math.abs(item.value) / botMax) * 80}
                        <div class="bar-row flex items-center w-full gap-2 flex-1 min-h-0">
                            <button
                                onclick={() => selectSymbol(item.symbol)}
                                title="{item.symbol}{nameMap[item.symbol] ? ' — ' + nameMap[item.symbol] : ''}"
                                aria-label="Select {item.symbol}{nameMap[item.symbol] ? ' - ' + nameMap[item.symbol] : ''}"
                                class="w-20 text-[14px] font-semibold text-text shrink-0 uppercase tracking-tighter truncate text-left hover:text-text transition-colors cursor-pointer"
                            >{item.symbol}</button>
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative flex justify-end">
                                <div class="h-full bg-down/8 border-r-2 border-down flex items-center justify-start rounded-sm relative transition-all duration-700 ease-out" style="width: {width}%">
                                    <span class="text-[12px] font-mono tabular-nums font-medium text-text whitespace-nowrap px-2 {width < 45 ? 'absolute right-full mr-1' : ''}">
                                        {(item.value ?? 0).toFixed(1)}%
                                    </span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        {:else}
            <div class="flex-1 flex items-center justify-center">
                <div class="w-4 h-4 border border-border border-t-text-muted rounded-full animate-spin" aria-hidden="true"></div>
            </div>
        {/if}
    </div>
</Card>

<style>
    :global(.top-movers-root) { container-type: inline-size; }

    div { user-select: none; }
    .transition-all { transition-property: all; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }

    @container (max-width: 280px) {
        .bar-row { min-height: 28px; }
    }
</style>