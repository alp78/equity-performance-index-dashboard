<!--
  ═══════════════════════════════════════════════════════════════════════════
   TechnicalLevels — RSI, MACD, Bollinger, ATR, Beta Gauges
  ═══════════════════════════════════════════════════════════════════════════
   Technical indicator panel for the currently selected stock.  Each
   metric is shown as a visual range bar with a status label (overbought,
   bullish, etc.) and a hover tooltip explaining the indicator.
   Skips loading when the dashboard is in overview or sector mode.

   Data source : GET /technicals/{symbol}?market_index={idx}
   Placement   : right sidebar below the chart (stock browse mode only)
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { browser } from '$app/environment';
    import { onMount, onDestroy } from 'svelte';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { selectedSymbol, marketIndex, isOverviewMode, isSectorMode, isMacroContextMode, summaryData } from '$lib/stores.js';
    import { INDEX_CONFIG } from '$lib/index-registry.js';
    import { getCached, setCached, isCacheFresh } from '$lib/cache.js';
    import { getSectorColor } from '$lib/theme.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let selectedSectorColor = $derived((() => {
        const sym = $selectedSymbol;
        if (!sym) return '';
        const t = ($summaryData.assets || []).find(a => a.symbol === sym);
        if (!t) return '';
        const sec = (t.sector && t.sector !== 0) ? t.sector : 'Other';
        return getSectorColor(sec);
    })());

    let data = $state(null);
    let loading = $state(false);
    let timer;
    let activeTooltip = $state(null);
    let tooltipTimer;

    let currentSymbol = $derived($selectedSymbol);
    let currentIndex = $derived($marketIndex);
    let skip = $derived($isOverviewMode || $isSectorMode || $isMacroContextMode);

    // ─── Period key for cache / API ─────────────────────────────────────
    let periodKey = $derived(
        customRange?.start ? `${customRange.start}_${customRange.end}` : (currentPeriod || '1y')
    );

    const STATUS_COLORS = {
        overbought: 'text-down',  oversold: 'text-warn',  neutral: 'text-up',
        bullish: 'text-up',       bearish: 'text-down',
        upper_band: 'text-warn',  lower_band: 'text-warn', mid_range: 'text-up',
        high: 'text-down',        moderate: 'text-warn',   low: 'text-up',
    };
    const STATUS_BG = {
        overbought: 'bg-down/8', oversold: 'bg-warn/8', neutral: 'bg-up/8',
        bullish: 'bg-up/8',      bearish: 'bg-down/8',
        upper_band: 'bg-warn/8', lower_band: 'bg-warn/8', mid_range: 'bg-up/8',
        high: 'bg-down/8',       moderate: 'bg-warn/8',  low: 'bg-up/8',
    };
    const STATUS_LABELS = {
        overbought: 'OVERBOUGHT', oversold: 'OVERSOLD', neutral: 'NEUTRAL',
        bullish: 'BULLISH', bearish: 'BEARISH',
        upper_band: 'UPPER', lower_band: 'LOWER', mid_range: 'MID',
        high: 'HIGH', moderate: 'MOD', low: 'LOW',
    };

    // Dynamic tooltip titles — adapt to period-based indicator params from API
    let params = $derived(data?.params || { rsi: 14, macd: [12, 26, 9], bb: 20, atr: 14 });

    let TOOLTIPS = $derived({
        rsi: {
            title: `RSI (${params.rsi})`,
            description: 'Relative Strength Index — momentum oscillator measuring speed and magnitude of recent price changes.',
            levels: [
                { label: 'Oversold', color: 'text-warn', range: '< 30', meaning: 'Price may be undervalued. Potential bounce.' },
                { label: 'Neutral', color: 'text-up', range: '30 – 70', meaning: 'Normal trading range. No extreme momentum.' },
                { label: 'Overbought', color: 'text-down', range: '> 70', meaning: 'Price may be overextended. Potential pullback.' },
            ],
        },
        macd: {
            title: `MACD (${params.macd.join(', ')})`,
            description: 'Moving Average Convergence Divergence — trend-following momentum indicator showing the relationship between two EMAs.',
            levels: [
                { label: 'Bullish', color: 'text-up', range: 'Hist > 0', meaning: 'MACD above signal line. Upward momentum.' },
                { label: 'Bearish', color: 'text-down', range: 'Hist < 0', meaning: 'MACD below signal line. Downward pressure.' },
            ],
        },
        bollinger: {
            title: `Bollinger %B (${params.bb}, 2)`,
            description: 'Position of price within Bollinger Bands. Shows if price is near upper/lower volatility envelope.',
            levels: [
                { label: 'Lower', color: 'text-warn', range: '< 0.2', meaning: 'Near lower band — oversold or breakdown risk.' },
                { label: 'Mid', color: 'text-up', range: '0.2 – 0.8', meaning: 'Within normal range. Price near the mean.' },
                { label: 'Upper', color: 'text-warn', range: '> 0.8', meaning: 'Near upper band — overbought or breakout.' },
            ],
        },
        atr: {
            title: `ATR (${params.atr})`,
            description: 'Average True Range — measures daily price volatility in absolute terms. Higher = more volatile.',
            levels: [
                { label: 'Low', color: 'text-up', range: '< 1.5%', meaning: 'Low volatility. Tight daily ranges.' },
                { label: 'Moderate', color: 'text-warn', range: '1.5 – 3%', meaning: 'Normal volatility for most stocks.' },
                { label: 'High', color: 'text-down', range: '> 3%', meaning: 'Wide daily swings. Higher risk/reward.' },
            ],
        },
        beta: {
            title: `Beta (vs S&P 500, ${params.rows || '?'}d)`,
            description: 'Sensitivity to market moves. Beta of 1.0 = moves with market. Higher = amplifies moves.',
            levels: [
                { label: 'Low', color: 'text-warn', range: '< 0.7', meaning: 'Defensive. Less volatile than the market.' },
                { label: 'Moderate', color: 'text-up', range: '0.7 – 1.3', meaning: 'Moves roughly in line with the market.' },
                { label: 'High', color: 'text-down', range: '> 1.3', meaning: 'Aggressive. Amplifies market swings.' },
            ],
        },
    });

    // ─── Data loading (follows TopMovers pattern: no reactive reads in async fn) ─

    const TECH_TTL = 5 * 60 * 1000;

    function cKey(sym, pk) { return `tech_${sym}_${pk}`; }

    // All values passed as params to avoid reactive reads inside async function
    async function doFetch(sym, pk, idx, period, range) {
        if (!browser || !sym) return;
        loading = true;
        try {
            let url = `${API_BASE_URL}/technicals/${encodeURIComponent(sym)}?market_index=${idx}`;
            if (range?.start) {
                url += `&start=${range.start}&end=${range.end}`;
            } else {
                url += `&period=${period || '1y'}`;
            }
            const res = await fetch(url);
            if (res.ok) {
                const d = await res.json();
                if (!d.error) {
                    data = d;
                    setCached(cKey(sym, pk), d, TECH_TTL);
                }
            }
        } catch {}
        loading = false;
    }

    // Reactive effect: refetch when symbol OR period changes
    let lastKey = '';

    $effect(() => {
        const sym = currentSymbol;
        const pk = periodKey;
        const idx = currentIndex || 'sp500';
        if (!sym || skip) return;
        const key = cKey(sym, pk);
        if (key === lastKey) return;
        lastKey = key;
        const cached = getCached(key);
        if (cached) { data = cached.data; }
        if (!isCacheFresh(key)) doFetch(sym, pk, idx, currentPeriod, customRange);
    });

    onMount(() => {
        timer = setInterval(() => {
            const sym = currentSymbol;
            const pk = periodKey;
            const idx = currentIndex || 'sp500';
            if (sym && !skip) doFetch(sym, pk, idx, currentPeriod, customRange);
        }, TECH_TTL);
    });
    onDestroy(() => { if (timer) clearInterval(timer); });

    // ─── Tooltip (portal to document.body) ──────────────────────────────

    let tooltipEl = $state(null);
    let tooltipStyle = $state('');

    function showTooltip(key, event) {
        if (tooltipTimer) clearTimeout(tooltipTimer);
        const rect = event.currentTarget.getBoundingClientRect();
        const ttW = 380;
        // Position above the row, right-aligned with the panel
        let left = rect.right - ttW;
        if (left < 8) left = 8;
        const bottom = window.innerHeight - rect.top + 6;
        tooltipStyle = `left:${left}px;bottom:${bottom}px;`;
        activeTooltip = key;
    }
    function hideTooltip() {
        tooltipTimer = setTimeout(() => { activeTooltip = null; }, 150);
    }
    function keepTooltip() {
        if (tooltipTimer) clearTimeout(tooltipTimer);
    }

    // Teleport tooltip to body so no parent overflow can clip it
    $effect(() => {
        if (tooltipEl && browser) {
            document.body.appendChild(tooltipEl);
        }
    });
    onDestroy(() => { tooltipEl?.remove(); });

    function fmtVal(val) {
        if (val == null) return '—';
        return val.toFixed(2);
    }

    function formatDateShort(dateStr) {
        if (!dateStr) return '';
        const d = new Date(dateStr + 'T00:00:00');
        const dd = String(d.getDate()).padStart(2, '0');
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const yy = String(d.getFullYear()).slice(2);
        return `${dd}/${mm}/${yy}`;
    }
</script>

<Card fill padding={false} class="overflow-hidden">

    <!-- header -->
    <div class="px-4 pt-4 pb-2">
        <SectionHeader title="Technical Levels" subtitle={data?.symbol || currentSymbol || '—'} subtitleColor={selectedSectorColor} border size="lg">
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
    </div>

    {#if loading && !data}
        <div class="flex-1 flex flex-col items-center justify-center opacity-40">
            <div class="w-5 h-5 border-2 border-border border-t-text-muted rounded-full animate-spin" aria-hidden="true"></div>
            <span class="text-[11px] font-semibold uppercase tracking-widest text-text-muted mt-2">Loading</span>
        </div>
    {:else if data}
        <div class="tech-rows flex-1 flex flex-col justify-around min-h-0 gap-1 px-2 pb-2">
            <!-- RSI -->
            <button class="w-full min-w-0 text-left"
                onmouseenter={(e) => showTooltip('rsi', e)}
                onmouseleave={hideTooltip}
            >
                <div class="flex items-center gap-2 px-3 py-2 rounded-xl border border-border-subtle hover:bg-bg-hover transition-colors cursor-default min-w-0">
                    <span class="text-[12px] font-semibold text-text uppercase tracking-wider w-12 shrink-0">RSI</span>
                    <div class="tech-mid flex-1 h-1.5 bg-border-subtle rounded-full overflow-hidden relative min-w-0">
                        <div class="absolute left-[30%] w-px h-full bg-border"></div>
                        <div class="absolute left-[70%] w-px h-full bg-border"></div>
                        {#if data.rsi?.value != null}
                            <div class="absolute top-0 h-full w-2 rounded-full {STATUS_BG[data.rsi.status]}"
                                style="left: calc({Math.min(Math.max(data.rsi.value, 0), 100)}% - 4px);"></div>
                            <div class="absolute top-0 h-full w-1.5 rounded-full transition-all"
                                style="left: calc({Math.min(Math.max(data.rsi.value, 0), 100)}% - 3px); background: {data.rsi.status === 'overbought' ? 'var(--color-down)' : data.rsi.status === 'oversold' ? 'var(--color-warn)' : 'var(--color-up)'};"></div>
                        {/if}
                    </div>
                    <span class="text-[length:var(--text-num-md)] font-bold tabular-nums text-text-secondary text-right shrink-0">{data.rsi?.value ?? '—'}</span>
                    <span class="text-[11px] font-bold uppercase px-1.5 py-0.5 rounded {STATUS_BG[data.rsi?.status]} {STATUS_COLORS[data.rsi?.status]} shrink-0">{STATUS_LABELS[data.rsi?.status] || '—'}</span>
                </div>
            </button>

            <!-- MACD -->
            <button class="w-full min-w-0 text-left"
                onmouseenter={(e) => showTooltip('macd', e)}
                onmouseleave={hideTooltip}
            >
                <div class="flex items-center gap-2 px-3 py-2 rounded-xl border border-border-subtle hover:bg-bg-hover transition-colors cursor-default min-w-0">
                    <span class="text-[12px] font-semibold text-text uppercase tracking-wider w-12 shrink-0">MACD</span>
                    <div class="tech-mid flex-1 flex items-center gap-1.5 min-w-0 overflow-hidden">
                        <span class="text-[12px] text-text-faint">{data.macd?.status === 'bullish' ? '▲' : '▼'}</span>
                        <span class="text-[length:var(--text-num-md)] text-text-faint truncate">sig {fmtVal(data.macd?.signal)}</span>
                    </div>
                    <span class="text-[length:var(--text-num-md)] font-bold tabular-nums text-text-secondary text-right shrink-0">{fmtVal(data.macd?.value)}</span>
                    <span class="text-[11px] font-bold uppercase px-1.5 py-0.5 rounded {STATUS_BG[data.macd?.status]} {STATUS_COLORS[data.macd?.status]} shrink-0">{STATUS_LABELS[data.macd?.status] || '—'}</span>
                </div>
            </button>

            <!-- Bollinger %B -->
            <button class="w-full min-w-0 text-left"
                onmouseenter={(e) => showTooltip('bollinger', e)}
                onmouseleave={hideTooltip}
            >
                <div class="flex items-center gap-2 px-3 py-2 rounded-xl border border-border-subtle hover:bg-bg-hover transition-colors cursor-default min-w-0">
                    <span class="text-[12px] font-semibold text-text uppercase tracking-wider w-12 shrink-0">BB %B</span>
                    <div class="tech-mid flex-1 h-1.5 bg-border-subtle rounded-full overflow-hidden relative min-w-0">
                        <div class="absolute left-[20%] w-px h-full bg-border"></div>
                        <div class="absolute left-[80%] w-px h-full bg-border"></div>
                        {#if data.bollinger?.percent_b != null}
                            {@const pct = Math.min(Math.max(data.bollinger.percent_b * 100, 0), 100)}
                            <div class="absolute top-0 h-full w-1.5 rounded-full transition-all"
                                style="left: calc({pct}% - 3px); background: {data.bollinger.status === 'upper_band' ? 'var(--color-warn)' : data.bollinger.status === 'lower_band' ? 'var(--color-warn)' : 'var(--color-up)'};"></div>
                        {/if}
                    </div>
                    <span class="text-[length:var(--text-num-md)] font-bold tabular-nums text-text-secondary text-right shrink-0">{data.bollinger?.percent_b != null ? data.bollinger.percent_b.toFixed(2) : '—'}</span>
                    <span class="text-[11px] font-bold uppercase px-1.5 py-0.5 rounded {STATUS_BG[data.bollinger?.status]} {STATUS_COLORS[data.bollinger?.status]} shrink-0">{STATUS_LABELS[data.bollinger?.status] || '—'}</span>
                </div>
            </button>

            <!-- ATR -->
            <button class="w-full min-w-0 text-left"
                onmouseenter={(e) => showTooltip('atr', e)}
                onmouseleave={hideTooltip}
            >
                <div class="flex items-center gap-2 px-3 py-2 rounded-xl border border-border-subtle hover:bg-bg-hover transition-colors cursor-default min-w-0">
                    <span class="text-[12px] font-semibold text-text uppercase tracking-wider w-12 shrink-0">ATR</span>
                    <div class="tech-mid flex-1 flex items-baseline gap-1.5 min-w-0 overflow-hidden">
                        {#if data.atr?.percent != null}
                            <span class="text-[length:var(--text-num-sm)] text-text-faint truncate">({data.atr.percent}%)</span>
                        {/if}
                    </div>
                    <span class="text-[length:var(--text-num-md)] font-bold tabular-nums text-text-secondary text-right shrink-0">{data.atr?.value != null ? data.atr.value.toFixed(2) : '—'}</span>
                    <span class="text-[11px] font-bold uppercase px-1.5 py-0.5 rounded {STATUS_BG[data.atr?.status]} {STATUS_COLORS[data.atr?.status]} shrink-0">{STATUS_LABELS[data.atr?.status] || '—'}</span>
                </div>
            </button>

            <!-- Beta -->
            <button class="w-full min-w-0 text-left"
                onmouseenter={(e) => showTooltip('beta', e)}
                onmouseleave={hideTooltip}
            >
                <div class="flex items-center gap-2 px-3 py-2 rounded-xl border border-border-subtle hover:bg-bg-hover transition-colors cursor-default min-w-0">
                    <span class="text-[12px] font-semibold text-text uppercase tracking-wider w-12 shrink-0">BETA</span>
                    <div class="tech-mid flex-1 flex items-baseline gap-1.5 min-w-0 overflow-hidden">
                        <span class="text-[12px] text-text-faint truncate">vs S&P</span>
                    </div>
                    <span class="text-[length:var(--text-num-md)] font-bold tabular-nums text-text-secondary text-right shrink-0">{data.beta?.value != null ? data.beta.value.toFixed(2) : '—'}</span>
                    <span class="text-[11px] font-bold uppercase px-1.5 py-0.5 rounded {STATUS_BG[data.beta?.status]} {STATUS_COLORS[data.beta?.status]} shrink-0">{STATUS_LABELS[data.beta?.status] || '—'}</span>
                </div>
            </button>
        </div>
    {:else}
        <div class="flex-1 flex items-center justify-center">
            <span class="text-[13px] text-text-muted">Select a stock to view technicals</span>
        </div>
    {/if}
</Card>

<!-- Tooltip — teleported to document.body via $effect -->
<div bind:this={tooltipEl} class="tt-portal" class:tt-visible={activeTooltip && TOOLTIPS[activeTooltip]}>
    {#if activeTooltip && TOOLTIPS[activeTooltip]}
        {@const tt = TOOLTIPS[activeTooltip]}
        <div class="tt-card" role="tooltip" style={tooltipStyle}
            onmouseenter={keepTooltip} onmouseleave={hideTooltip}>
            <div class="text-[14px] font-semibold text-text uppercase tracking-wider mb-1">{tt.title}</div>
            <div class="text-[13px] text-text-muted leading-relaxed mb-2.5">{tt.description}</div>
            {#each tt.levels as lvl}
                <div class="flex items-start gap-2 mb-1.5">
                    <span class="text-[12px] font-bold uppercase w-20 shrink-0 {lvl.color}">{lvl.label}</span>
                    <span class="text-[14px] text-text-faint w-16 shrink-0">{lvl.range}</span>
                    <span class="text-[13px] text-text-muted leading-snug">{lvl.meaning}</span>
                </div>
            {/each}
        </div>
    {/if}
</div>

<style>
    .tech-rows { container-type: inline-size; }

    @container (max-width: 220px) {
        .tech-mid { display: none; }
    }

    .tt-portal {
        display: contents;
    }
    .tt-card {
        position: fixed;
        z-index: 9999;
        width: min(380px, calc(100vw - 2rem));
        padding: var(--space-md) var(--space-lg);
        background: var(--surface-overlay);
        border: 1px solid var(--border-default);
        border-radius: var(--radius-lg);
        backdrop-filter: blur(8px);
        box-shadow: var(--shadow-tooltip);
        opacity: 0;
        animation: tt-fade 0.12s ease-out forwards;
    }
    @keyframes tt-fade {
        to { opacity: 1; }
    }
</style>
