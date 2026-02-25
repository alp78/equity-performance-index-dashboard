<!--
  ═══════════════════════════════════════════════════════════════════════════
   MarketLeaders — "Most Active" Bottom Panel
  ═══════════════════════════════════════════════════════════════════════════
   Displays the top 3 stocks ranked by average daily volume for the
   selected period.  Fetches from /most-active endpoint.  Responds to
   period switches and custom date ranges just like TopMovers.

   Data source : GET /most-active?period={p}&index={idx}
                 GET /most-active?start=...&end=...&index={idx}
   Placement   : bottom panel grid, col-span-3
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { browser } from '$app/environment';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { marketIndex, INDEX_CONFIG, currentCurrency, selectedSymbol, requestFocusSymbol } from '$lib/stores.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let topStocks = $state([]);
    let loading = $state(false);
    let cache = {};
    let abortController = null;

    let currentIndex = $derived($marketIndex);
    let currency = $derived($currentCurrency);
    let indexLabel = $derived(INDEX_CONFIG[currentIndex]?.label || currentIndex);
    let indexFlag = $derived(INDEX_CONFIG[currentIndex]?.flag || '');

    function selectStock(symbol) {
        selectedSymbol.set(symbol);
        requestFocusSymbol(symbol);
    }

    function formatDateShort(dateStr) {
        if (!dateStr) return '';
        const d = new Date(dateStr + 'T00:00:00');
        return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
    }

    // --- DATA LOADING (follows TopMovers pattern) ---
    async function load(period, index, range) {
        if (!browser) return;

        let url, cacheKey;
        if (range?.start) {
            cacheKey = `${index}_custom_${range.start}_${range.end}`;
            url = `${API_BASE_URL}/most-active?start=${range.start}&end=${range.end}&index=${index}`;
        } else if (period) {
            cacheKey = `${index}_${period}`;
            url = `${API_BASE_URL}/most-active?period=${period}&index=${index}`;
        } else {
            return;
        }

        if (cache[cacheKey]?.length) {
            topStocks = cache[cacheKey];
            return;
        }

        if (abortController) abortController.abort();
        abortController = new AbortController();
        loading = true;

        try {
            const res = await fetch(url, { signal: abortController.signal });
            if (res.ok) {
                const data = await res.json();
                if (data?.length) {
                    topStocks = data;
                    cache[cacheKey] = data;
                }
            }
        } catch (err) {
            if (err.name !== 'AbortError') console.error('Most-active error:', err);
        }
        loading = false;
    }

    let lastIndex = '';

    $effect(() => {
        const idx = currentIndex;
        if (idx !== lastIndex) {
            lastIndex = idx;
            topStocks = [];
        }
        load(currentPeriod, idx, customRange);
    });

    let maxVol = $derived(topStocks.length ? topStocks[0].avg_volume : 1);

    function fmtVol(v) {
        if (!v) return '—';
        if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B';
        if (v >= 1e6) return (v / 1e6).toFixed(0) + 'M';
        if (v >= 1e3) return (v / 1e3).toFixed(0) + 'K';
        return v.toString();
    }

    function fmtPrice(p) {
        if (!p) return '—';
        return p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
</script>

<Card fill class="most-active-root">

    <!-- header -->
    <SectionHeader title="Most Active" subtitle={indexLabel} subtitleFlag={indexFlag} border size="lg">
        {#snippet action()}
            {#if customRange?.start}
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

    <!-- stock rows -->
    <div class="flex-1 flex flex-col justify-around min-h-0 overflow-y-auto">
        {#if topStocks.length}
            {#each topStocks as stock}
                {@const pct = stock.period_return ?? stock.daily_change_pct ?? 0}
                {@const volWidth = Math.max((stock.avg_volume / maxVol) * 100, 8)}
                <div class="flex flex-col gap-1 py-1.5">
                    <!-- row 1: symbol + price -->
                    <div class="stock-row flex items-center justify-between gap-2">
                        <div class="flex items-baseline gap-2 min-w-0 flex-1">
                            <button
                                onclick={() => selectStock(stock.symbol)}
                                title="{stock.symbol}{stock.name ? ' — ' + stock.name : ''}"
                                class="text-[14px] font-semibold text-text uppercase tracking-tight hover:text-text transition-colors cursor-pointer shrink-0"
                            >{stock.symbol}</button>
                            <span class="text-[11px] text-text-faint truncate">{stock.name || ''}</span>
                        </div>
                        <div class="flex items-baseline gap-2 shrink-0">
                            <span class="text-[14px] font-mono font-medium text-text-secondary tabular-nums">
                                <span class="text-text-muted">{currency}</span>{fmtPrice(stock.last_price)}
                            </span>
                            <span class="text-[12px] font-mono font-medium tabular-nums {pct >= 0 ? 'text-up' : 'text-down'}">
                                {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
                            </span>
                        </div>
                    </div>
                    <!-- row 2: avg daily volume bar -->
                    <div class="flex items-center gap-2">
                        <span class="text-[10px] font-medium text-text-faint uppercase tracking-wider w-7 shrink-0">Vol</span>
                        <div class="flex-1 h-2 rounded-sm bg-border-subtle overflow-hidden">
                            <div class="h-full rounded-sm bg-accent/15 transition-all duration-500" style="width: {volWidth}%"></div>
                        </div>
                        <span class="text-[11px] font-mono font-medium text-text-muted tabular-nums w-10 text-right shrink-0">{fmtVol(stock.avg_volume)}</span>
                    </div>
                </div>
            {/each}
        {:else}
            <div class="flex-1 flex items-center justify-center">
                <div class="w-4 h-4 border border-border border-t-text-muted rounded-full animate-spin"></div>
            </div>
        {/if}
    </div>
</Card>

<style>
    :global(.most-active-root) { container-type: inline-size; }

    div { user-select: none; }

    @container (max-width: 280px) {
        .stock-row { flex-direction: column; align-items: flex-start; gap: 2px; }
    }
</style>
