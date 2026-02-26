<!--
  ═══════════════════════════════════════════════════════════════════════════
   IndexPerformanceTable — Multi-Index Stats Comparison Grid
  ═══════════════════════════════════════════════════════════════════════════
   Table comparing all 6 market indices: price, daily change, YTD return,
   52-week range (visual bar), period return, and annualised volatility.
   Rows animate position on re-sort.  Market open/closed status shown via
   green/red dot next to the exchange name.  Retries with 3 s backoff
   while the backend finishes loading index_prices from BigQuery.

   Data source : GET /index-prices/stats?period={p}
   Placement   : main chart area — "Global Macro" overview mode
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { browser } from '$app/environment';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { overviewSelectedIndices, INDEX_KEY_TO_TICKER } from '$lib/stores.js';
    import { MARKET_HOURS, INDEX_META_BY_TICKER as INDEX_META } from '$lib/index-registry.js';

    let { currentPeriod = '1y', customRange = null, highlightSymbol = null, highlightPair = null, onRowClick = null } = $props();

    let highlightSet = $derived((() => {
        if (highlightSymbol) return new Set([highlightSymbol]);
        if (highlightPair) {
            const t1 = INDEX_KEY_TO_TICKER[highlightPair.row];
            const t2 = INDEX_KEY_TO_TICKER[highlightPair.col];
            return new Set([t1, t2].filter(Boolean));
        }
        return null;
    })());

    let stats = $state([]);
    let loading = $state(false);
    let hasEverLoaded = $state(false);
    let cache = {};
    let now = $state(new Date());

    // refresh clock every 15s for local time + market open/closed
    $effect(() => {
        if (!browser) return;
        const iv = setInterval(() => { now = new Date(); }, 15000);
        return () => clearInterval(iv);
    });

    let selected = $derived(new Set($overviewSelectedIndices));

    function isMarketOpen(marketKey, time) {
        const market = MARKET_HOURS[marketKey];
        if (!market || !market.tz) return false;
        try {
            const tzNow = new Date(time.toLocaleString('en-US', { timeZone: market.tz }));
            const day = tzNow.getDay();
            if (day < 1 || day > 5) return false;
            const mins = tzNow.getHours() * 60 + tzNow.getMinutes();
            return mins >= (market.open.h * 60 + market.open.m) && mins < (market.close.h * 60 + market.close.m);
        } catch { return false; }
    }

    function localTime(marketKey, time) {
        const market = MARKET_HOURS[marketKey];
        if (!market || !market.tz) return { day: '', h: '—', m: '' };
        try {
            const day = time.toLocaleDateString('en-US', { timeZone: market.tz, weekday: 'short' });
            const hm = time.toLocaleTimeString('en-GB', { timeZone: market.tz, hour: '2-digit', minute: '2-digit', hour12: false });
            const [h, m] = hm.split(':');
            return { day, h, m };
        } catch { return { day: '', h: '—', m: '' }; }
    }

    // --- PERIOD LABEL ---

    function fmtDate(d) {
        if (!d) return '';
        const dt = new Date(d + 'T00:00:00');
        return `${dt.getDate()} ${dt.toLocaleDateString('en-GB', { month: 'short' })} '${String(dt.getFullYear()).slice(2)}`;
    }

    let isCustom = $derived(!!(customRange && customRange.start));
    let periodTag = $derived(isCustom ? 'C' : (currentPeriod || '1y').toUpperCase());

    function headerLabel(period, range) {
        if (range && range.start && range.end) return `${fmtDate(range.start)} → ${fmtDate(range.end)}`;
        if (!period) return '1Y';
        return period.toUpperCase();
    }

    // --- DATA LOADING ---

    let _retryTimer;

    async function load(period, range) {
        if (!browser) return;
        let url, cacheKey;
        if (range && range.start && range.end) {
            cacheKey = `stats_custom_${range.start}_${range.end}`;
            url = `${API_BASE_URL}/index-prices/stats?start=${range.start}&end=${range.end}&t=${Date.now()}`;
        } else {
            const p = period || '1y';
            cacheKey = `stats_${p}`;
            url = `${API_BASE_URL}/index-prices/stats?period=${p}&t=${Date.now()}`;
        }
        if (cache[cacheKey]) { stats = cache[cacheKey]; hasEverLoaded = true; return; }
        loading = true;
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            const res = await fetch(url, { signal: controller.signal });
            clearTimeout(timeout);
            if (res.ok) {
                const data = await res.json();
                if (data && data.length > 0) {
                    cache[cacheKey] = data;
                    stats = data;
                    hasEverLoaded = true;
                    loading = false;
                    return;
                }
            }
        } catch (e) {}
        loading = false;
        // Empty result — backend may still be loading, retry in 3s
        if (stats.length === 0) {
            if (_retryTimer) clearTimeout(_retryTimer);
            _retryTimer = setTimeout(() => load(period, range), 3000);
        } else {
            hasEverLoaded = true;
        }
    }

    $effect(() => { load(currentPeriod, customRange); return () => { if (_retryTimer) clearTimeout(_retryTimer); }; });

    // --- DERIVED TABLE DATA ---
    // sorted by period return desc; rank map drives animated row reordering
    let sortedStats = $derived(
        stats.filter(s => INDEX_META[s.symbol])
             .sort((a, b) => (b.period_return_pct || 0) - (a.period_return_pct || 0))
    );
    let rankMap = $derived(
        Object.fromEntries(sortedStats.map((s, i) => [s.symbol, i]))
    );
    let allStats = $derived(
        stats.filter(s => INDEX_META[s.symbol])
    );

    // --- FORMATTERS ---

    const COL = [20, 16, 11, 11, 12, 17, 13];

    function fmt(val, decimals = 2) {
        if (val == null || isNaN(val)) return '—';
        return val.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    }
    function fmtPrice(val, ccy) {
        if (val == null) return '—';
        if (val >= 10000) return ccy + val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        return ccy + val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    // % distance from 52-week high (0 = at high, negative = below)
    function fromHigh(current, high) {
        if (current == null || high == null || high === 0) return null;
        return ((current - high) / high) * 100;
    }

    // grayscale intensity: brighter = larger magnitude (like heatmap cells)
    // cap = value at which intensity maxes out
    function grayColor(val, cap) {
        if (val == null) return 'color:var(--text-disabled)';
        const t = Math.min(Math.abs(val) / cap, 1);
        const a = (0.35 + t * 0.6).toFixed(2);
        return `color:var(--text-primary); opacity:${a}`;
    }
    // bold threshold: day ≥ 1.5%, YTD ≥ 8%
    function isBold(val, threshold) {
        return val != null && Math.abs(val) >= threshold;
    }
</script>

<Card fill padding={false} class="perf-root bg-bg-hover">

    <!-- header -->
    <div class="px-5 pt-5 pb-3 flex-shrink-0 border-b border-border">
        <SectionHeader title="Index Performance Table" subtitle="Key metrics across indices">
            {#snippet action()}
                <div class="flex items-center gap-2">
                    <span class="text-[10px] font-semibold text-accent uppercase tracking-wider">{headerLabel(currentPeriod, customRange)}</span>
                    {#if loading}
                        <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin" aria-hidden="true"></div>
                    {/if}
                </div>
            {/snippet}
        </SectionHeader>
    </div>

  <div class="flex-1 min-h-0 flex flex-col overflow-x-auto" role="table" aria-label="Index performance comparison">
    <!-- column headers -->
    <div class="grid-header perf-min-w flex items-center text-text-faint uppercase tracking-wider font-semibold px-2 shrink-0 bg-surface-2" role="row">
        <div style="width:{COL[0]}%" class="pl-4 pr-1 text-left" role="columnheader">Index</div>
        <div style="width:{COL[1]}%" class="px-1 text-right" role="columnheader">Price</div>
        <div style="width:{COL[2]}%" class="px-1 text-right" role="columnheader">Day</div>
        <div style="width:{COL[3]}%" class="px-1 text-right" role="columnheader">YTD</div>
        <div style="width:{COL[4]}%" class="px-1 text-right" role="columnheader">Δ Hi</div>
        <div style="width:{COL[5]}%" class="px-1 text-right" role="columnheader">Return <span class="text-accent period-tag">{periodTag}</span></div>
        <div style="width:{COL[6]}%" class="px-1 text-right" role="columnheader">Vol <span class="text-accent period-tag">{periodTag}</span></div>
    </div>

    <!-- data rows — absolutely positioned for animated rank reordering -->
    <div class="flex-1 overflow-hidden px-2 relative rows-container perf-min-w">
        {#each allStats as row (row.symbol)}
            {@const meta = INDEX_META[row.symbol] || {}}
            {@const isSelected = selected.has(row.symbol)}
            {@const deltaHi = fromHigh(row.current_price, row.high_52w)}
            {@const open = isMarketOpen(meta.market, now)}
            {@const rank = rankMap[row.symbol] ?? 0}
            <div
                class="data-row absolute left-0 right-0 flex items-center border-b border-border-subtle hover:bg-surface-2 transition-colors cursor-pointer"
                style="top: calc({rank} * (100% / 6)); height: calc(100% / 6); opacity: {highlightSet ? (highlightSet.has(row.symbol) ? 1 : 0.15) : (isSelected ? 1 : 0.25)}; transition: top 0.5s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.15s;"
                onclick={() => onRowClick?.(meta.key || '', row.symbol)}
                role="row"
                tabindex="0"
                onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick?.(meta.key || '', row.symbol); } }}
            >
                <div style="width:{COL[0]}%" class="pl-4 pr-1">
                    <span class="font-semibold idx-name truncate uppercase tracking-wider" style="color:{meta.color || '#A1A1AA'}">{meta.short || meta.name || row.name}</span>
                </div>

                <div style="width:{COL[1]}%" class="px-1 text-right tabular-nums text-text-secondary font-medium val-text">
                    <span class="text-text-muted">{meta.ccy || ''}</span>{fmtPrice(row.current_price, '')}
                </div>

                <div style="width:{COL[2]}%; {grayColor(row.daily_change_pct, 3)}" class="px-1 text-right tabular-nums val-text font-medium">
                    {row.daily_change_pct >= 0 ? '+' : ''}{fmt(row.daily_change_pct)}%
                </div>

                <div style="width:{COL[3]}%; {grayColor(row.ytd_return_pct, 25)}" class="px-1 text-right tabular-nums val-text font-medium">
                    {row.ytd_return_pct >= 0 ? '+' : ''}{fmt(row.ytd_return_pct)}%
                </div>

                <div style="width:{COL[4]}%; {grayColor(deltaHi != null ? -deltaHi : null, 20)}" class="px-1 text-right tabular-nums val-text font-medium">
                    {deltaHi != null ? (deltaHi === 0 ? '0.0' : (deltaHi > 0 ? '+' : '') + deltaHi.toFixed(1)) + '%' : '—'}
                </div>

                <div style="width:{COL[5]}%; {grayColor(row.period_return_pct, 25)}" class="px-1 text-right tabular-nums val-text font-medium">
                    {row.period_return_pct >= 0 ? '+' : ''}{fmt(row.period_return_pct)}%
                </div>

                <div style="width:{COL[6]}%" class="px-1 text-right tabular-nums text-text-muted font-medium val-text">
                    {fmt(row.volatility_pct, 1)}%
                </div>
            </div>
        {/each}

        {#if allStats.length === 0}
            <div class="absolute inset-0 flex items-center justify-center">
                <div class="w-4 h-4 border border-border border-t-text-muted rounded-full animate-spin"></div>
            </div>
        {/if}
    </div>
  </div>
</Card>

<style>
    :global(.perf-root) { container-type: inline-size; }
    .rows-container { overflow: hidden; min-height: 180px; }

    .grid-header { font-size: clamp(11px, 1.8cqw, 13px); padding-top: 5px; padding-bottom: 5px; border-bottom: 1px solid transparent; }
    .grid-header > div { white-space: nowrap; }
    .period-tag { font-size: 10px; font-weight: 400; }
    .idx-name { font-size: clamp(13px, 2cqw, 15px); }
    .val-text { font-size: clamp(13px, 2cqw, 15px); }

    /* On narrow viewports, ensure table has minimum width for horizontal scroll */
    @media (max-width: 768px) {
        .perf-min-w { min-width: 580px; }
    }
</style>