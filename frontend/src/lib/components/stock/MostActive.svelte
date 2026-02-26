<!--
  ═══════════════════════════════════════════════════════════════════════════
   UnusualVolume — "Unusual Volume" Bottom Panel
  ═══════════════════════════════════════════════════════════════════════════
   Displays the top 3 stocks ranked by volume ratio (period average volume
   / all-time baseline volume).  Surfaces unusual activity that may signal
   institutional interest, news-driven moves, or breakout setups.
   Complements TopMovers (price story) with a volume story.

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
    import { marketIndex, currentCurrency, selectedSymbol, requestFocusSymbol } from '$lib/stores.js';
    import { INDEX_CONFIG } from '$lib/index-registry.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let topStocks = $state([]);
    let loading = $state(false);
    let cache = {};
    let abortController = null;

    let currentIndex = $derived($marketIndex);
    let currency = $derived($currentCurrency);
    let indexLabel = $derived(INDEX_CONFIG[currentIndex]?.abbr || currentIndex);
    let indexFlag = $derived(INDEX_CONFIG[currentIndex]?.flag || '');

    function selectStock(symbol) {
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

    let maxRatio = $derived(topStocks.length ? Math.max(...topStocks.map(s => s.volume_ratio || 1)) : 1);

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
    <SectionHeader title="Volume Surge" subtitle={indexLabel} subtitleFlag={indexFlag} border size="lg">
        {#snippet tooltip()}
            <div class="tt-title">Volume Ratio</div>
            <div class="tt-desc">Stocks ranked by how much their recent trading volume exceeds their historical average.</div>
            <div class="tt-row"><span class="tt-label">Ratio</span><span class="tt-meaning">Average daily volume over the period divided by the stock's all-time average daily volume</span></div>
            <div class="tt-row"><span class="tt-label">2.0×</span><span class="tt-meaning">Trading activity is double the historical norm</span></div>
            <div class="tt-row"><span class="tt-label">Rank</span><span class="tt-meaning">Ordered from highest to lowest volume ratio</span></div>
        {/snippet}
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
                {@const ratio = stock.volume_ratio ?? 1}
                {@const barWidth = Math.max(Math.min((ratio / maxRatio) * 100, 100), 8)}
                {@const pct = stock.period_return ?? stock.daily_change_pct ?? 0}
                {@const ratioColor = ratio >= 2.0 ? 'text-up' : ratio >= 1.3 ? 'text-warn' : 'text-text-secondary'}
                {@const barColor = ratio >= 2.0 ? 'bg-up/15' : ratio >= 1.3 ? 'bg-warn/15' : 'bg-accent/15'}
                <div class="flex flex-col gap-1 py-1.5">
                    <!-- row 1: symbol + volume ratio badge + return -->
                    <div class="stock-row flex items-center justify-between gap-2">
                        <div class="flex items-baseline gap-2 min-w-0 flex-1">
                            <button
                                onclick={() => selectStock(stock.symbol)}
                                title="{stock.symbol}{stock.name ? ' — ' + stock.name : ''}"
                                class="text-[14px] font-semibold text-text uppercase tracking-tight hover:text-text transition-colors cursor-pointer shrink-0"
                            >{stock.symbol}</button>
                            <span class="text-[11px] text-text-faint truncate">{stock.name || ''}</span>
                        </div>
                        <div class="flex items-center gap-2.5 shrink-0">
                            <span class="text-[length:var(--text-num-lg)] font-bold tabular-nums {ratioColor}">
                                {ratio.toFixed(1)}x
                            </span>
                            <span class="text-[length:var(--text-num-sm)] font-medium tabular-nums {pct >= 0 ? 'text-up' : 'text-down'} opacity-70">
                                {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                            </span>
                        </div>
                    </div>
                    <!-- row 2: volume bar (period avg vs baseline) -->
                    <div class="flex items-center gap-2">
                        <span class="text-[9px] font-medium text-text-faint uppercase tracking-wider shrink-0 w-7">{fmtVol(stock.avg_volume)}</span>
                        <div class="flex-1 h-2 rounded-sm bg-border-subtle overflow-hidden">
                            <div class="h-full rounded-sm {barColor} transition-all duration-500" style="width: {barWidth}%"></div>
                        </div>
                        <span class="text-[9px] font-medium text-text-faint tabular-nums shrink-0">avg {fmtVol(stock.baseline_volume)}</span>
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
