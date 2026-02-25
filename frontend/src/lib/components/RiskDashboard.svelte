<!--
  ═══════════════════════════════════════════════════════════════════════════
   RiskDashboard — Macro Risk Indicator Pills
  ═══════════════════════════════════════════════════════════════════════════
   Horizontal pill strip showing VIX, EU Vol, MOVE, Yield Curve, Credit
   Spread, and USD strength.  Each pill is colour-coded (green/amber/red)
   by risk level and expandable on hover/focus to show a detailed tooltip
   with value ranges and interpretation.  Sets riskHighlight store on
   hover so MacroWatchlist can highlight the corresponding row.

   Data source : GET /macro/risk-summary  (5-min cache)
   Placement   : bottom panel grid — "Global Macro" overview mode
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { onMount, onDestroy } from 'svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { riskHighlight, getCached, setCached, isCacheFresh } from '$lib/stores.js';

    let data = $state(null);
    let loading = $state(true);
    let timer;
    let activeTooltip = $state(null);
    const RISK_TTL = 5 * 60 * 1000;

    async function load() {
        try {
            const res = await fetch(`${API_BASE_URL}/macro/risk-summary`);
            if (res.ok) {
                data = await res.json();
                setCached('macro_risk', data, RISK_TTL);
            }
        } catch {}
        loading = false;
    }

    onMount(() => {
        const cached = getCached('macro_risk');
        if (cached) { data = cached.data; loading = false; }
        if (!isCacheFresh('macro_risk')) load();
        timer = setInterval(load, RISK_TTL);
    });

    onDestroy(() => { if (timer) clearInterval(timer); });

    const VIX_COLORS = { low: 'text-up', normal: 'text-up', elevated: 'text-warn', high: 'text-down', unknown: 'text-text-faint' };
    const VIX_BG = { low: 'bg-up/8', normal: 'bg-up/8', elevated: 'bg-warn/8', high: 'bg-down/8', unknown: 'bg-bg-hover' };
    const EUVOL_COLORS = { low: 'text-up', normal: 'text-up', elevated: 'text-warn', high: 'text-down', unknown: 'text-text-faint' };
    const EUVOL_BG = { low: 'bg-up/8', normal: 'bg-up/8', elevated: 'bg-warn/8', high: 'bg-down/8', unknown: 'bg-bg-hover' };
    const MOVE_COLORS = { low: 'text-up', normal: 'text-up', elevated: 'text-warn', high: 'text-down', unknown: 'text-text-faint' };
    const MOVE_BG = { low: 'bg-up/8', normal: 'bg-up/8', elevated: 'bg-warn/8', high: 'bg-down/8', unknown: 'bg-bg-hover' };
    const CURVE_COLORS = { normal: 'text-up', flat: 'text-warn', inverted: 'text-down', unknown: 'text-text-faint' };
    const CURVE_BG = { normal: 'bg-up/8', flat: 'bg-warn/8', inverted: 'bg-down/8', unknown: 'bg-bg-hover' };
    const CREDIT_COLORS = { tightening: 'text-up', stable: 'text-text-muted', widening: 'text-down', unknown: 'text-text-faint' };
    const CREDIT_BG = { tightening: 'bg-up/8', stable: 'bg-bg-hover', widening: 'bg-down/8', unknown: 'bg-bg-hover' };
    const USD_COLORS = { strengthening: 'text-up', stable: 'text-text-muted', weakening: 'text-down' };
    const USD_BG = { strengthening: 'bg-up/8', stable: 'bg-bg-hover', weakening: 'bg-down/8' };

    const TOOLTIPS = {
        vix: {
            title: 'CBOE Volatility Index (VIX)',
            description: 'Measures expected 30-day volatility of the S&P 500, derived from options prices. Often called the "fear gauge".',
            levels: [
                { label: 'Low', color: 'text-up', range: '< 15', meaning: 'Markets calm, low expected volatility. Complacency risk.' },
                { label: 'Normal', color: 'text-up', range: '15 – 20', meaning: 'Typical market conditions, moderate uncertainty.' },
                { label: 'Elevated', color: 'text-warn', range: '20 – 30', meaning: 'Heightened fear, above-average hedging activity.' },
                { label: 'High', color: 'text-down', range: '> 30', meaning: 'Extreme fear or panic. Often seen during crises.' },
            ],
        },
        eu_vol: {
            title: 'EURO STOXX 50 Realized Volatility',
            description: '30-day annualized realized volatility of the EURO STOXX 50 index. Computed from daily returns — measures actual European equity market turbulence.',
            levels: [
                { label: 'Low', color: 'text-up', range: '< 12%', meaning: 'European markets calm, minimal daily swings.' },
                { label: 'Normal', color: 'text-up', range: '12 – 20%', meaning: 'Typical daily fluctuations for European equities.' },
                { label: 'Elevated', color: 'text-warn', range: '20 – 30%', meaning: 'Above-average turbulence — geopolitical or macro stress.' },
                { label: 'High', color: 'text-down', range: '> 30%', meaning: 'Severe European market stress. Crisis-level volatility.' },
            ],
        },
        curve: {
            title: '10Y–2Y Treasury Spread',
            description: 'Difference between 10-year and 2-year US Treasury yields. A key recession predictor — has inverted before every US recession in 50 years.',
            levels: [
                { label: 'Normal', color: 'text-up', range: '> +0.50%', meaning: 'Healthy upward slope. Growth and inflation expectations intact.' },
                { label: 'Flat', color: 'text-warn', range: '-0.10% to +0.50%', meaning: 'Narrowing spread signals uncertainty about growth outlook.' },
                { label: 'Inverted', color: 'text-down', range: '< -0.10%', meaning: 'Short rates above long rates. Strong recession signal (6–24 month lead).' },
            ],
        },
        credit: {
            title: 'High-Yield Credit Spread (ICE BofA)',
            description: 'Spread between high-yield corporate bonds and Treasuries. Measures market appetite for credit risk.',
            levels: [
                { label: 'Tightening', color: 'text-up', range: 'Spread falling', meaning: 'Investors confident, reaching for yield. Risk-on environment.' },
                { label: 'Stable', color: 'text-text-muted', range: 'No significant move', meaning: 'Credit conditions steady. No notable shift in risk appetite.' },
                { label: 'Widening', color: 'text-down', range: 'Spread rising', meaning: 'Growing fear of defaults. Flight to safety — risk-off signal.' },
            ],
        },
        move: {
            title: 'ICE BofA MOVE Index',
            description: 'Measures implied volatility of US Treasury options across maturities. The bond market equivalent of the VIX — spikes signal fixed-income stress.',
            levels: [
                { label: 'Low', color: 'text-up', range: '< 80', meaning: 'Bond markets calm. Low rate volatility expectations.' },
                { label: 'Normal', color: 'text-up', range: '80 – 100', meaning: 'Typical Treasury vol. Standard rate uncertainty.' },
                { label: 'Elevated', color: 'text-warn', range: '100 – 130', meaning: 'Above-average bond stress. Rate policy uncertainty rising.' },
                { label: 'High', color: 'text-down', range: '> 130', meaning: 'Severe fixed-income volatility. Often coincides with rate shocks or crises.' },
            ],
        },
        usd: {
            title: 'USD Strength (FX Average)',
            description: 'Average change across major FX pairs (EUR, GBP, JPY, CNY, INR, CHF, AUD). Indicates broad dollar direction.',
            levels: [
                { label: 'Strengthening', color: 'text-up', range: 'Avg change > +0.3%', meaning: 'Capital flowing into USD. Can pressure EM and commodities.' },
                { label: 'Stable', color: 'text-text-muted', range: '-0.3% to +0.3%', meaning: 'No strong directional bias in the dollar.' },
                { label: 'Weakening', color: 'text-down', range: 'Avg change < -0.3%', meaning: 'Dollar losing ground. Supportive for EM assets and commodities.' },
            ],
        },
    };

    let tooltipTimer;

    function showTooltip(key) {
        if (tooltipTimer) clearTimeout(tooltipTimer);
        activeTooltip = key;
        riskHighlight.set(key);
    }

    function hideTooltip() {
        tooltipTimer = setTimeout(() => { activeTooltip = null; riskHighlight.set(null); }, 150);
    }

    function keepTooltip() {
        if (tooltipTimer) clearTimeout(tooltipTimer);
    }
</script>

{#if !loading && data}
    <div class="risk-dash-root flex items-center gap-1.5">
        <!-- VIX -->
        <div class="relative" role="button"
            onmouseenter={() => showTooltip('vix')}
            onmouseleave={hideTooltip}
            onfocus={() => showTooltip('vix')} onblur={hideTooltip}
            tabindex="0"
        >
            <div class="flex items-center gap-1.5 pill rounded-lg border border-border {VIX_BG[data.vix.level]} cursor-default">
                <span class="text-[11px] font-semibold text-text-muted uppercase tracking-wider">VIX</span>
                <span class="text-[13px] font-mono font-bold tabular-nums {VIX_COLORS[data.vix.level]}">{data.vix.value != null ? data.vix.value.toFixed(1) : '—'}</span>
                <span class="text-[10px] font-bold uppercase tracking-wide {VIX_COLORS[data.vix.level]}">{data.vix.level}</span>
            </div>
            {#if activeTooltip === 'vix'}
                <div class="tt-card" role="tooltip" onmouseenter={keepTooltip} onmouseleave={hideTooltip}>
                    <div class="text-[13px] font-semibold text-text uppercase tracking-wider mb-1">{TOOLTIPS.vix.title}</div>
                    <div class="text-[12px] text-text-muted leading-relaxed mb-2">{TOOLTIPS.vix.description}</div>
                    {#each TOOLTIPS.vix.levels as lvl}
                        <div class="flex items-start gap-2 mb-1.5">
                            <div class="flex flex-col w-16 shrink-0">
                                <span class="text-[12px] font-bold uppercase {lvl.color}">{lvl.label}</span>
                                <span class="text-[11px] font-mono text-text-faint">{lvl.range}</span>
                            </div>
                            <span class="text-[12px] text-text-muted leading-snug">{lvl.meaning}</span>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>

        <!-- EU Vol -->
        {#if data.eu_vol && data.eu_vol.value != null}
        <div class="relative" role="button"
            onmouseenter={() => showTooltip('eu_vol')}
            onmouseleave={hideTooltip}
            onfocus={() => showTooltip('eu_vol')} onblur={hideTooltip}
            tabindex="0"
        >
            <div class="flex items-center gap-1.5 pill rounded-lg border border-border {EUVOL_BG[data.eu_vol.level]} cursor-default">
                <span class="text-[11px] font-semibold text-text-muted uppercase tracking-wider">EU Vol</span>
                <span class="text-[13px] font-mono font-bold tabular-nums {EUVOL_COLORS[data.eu_vol.level]}">{data.eu_vol.value != null ? data.eu_vol.value.toFixed(1) + '%' : '—'}</span>
                <span class="text-[10px] font-bold uppercase tracking-wide {EUVOL_COLORS[data.eu_vol.level]}">{data.eu_vol.level}</span>
            </div>
            {#if activeTooltip === 'eu_vol'}
                <div class="tt-card" role="tooltip" onmouseenter={keepTooltip} onmouseleave={hideTooltip}>
                    <div class="text-[13px] font-semibold text-text uppercase tracking-wider mb-1">{TOOLTIPS.eu_vol.title}</div>
                    <div class="text-[12px] text-text-muted leading-relaxed mb-2">{TOOLTIPS.eu_vol.description}</div>
                    {#each TOOLTIPS.eu_vol.levels as lvl}
                        <div class="flex items-start gap-2 mb-1.5">
                            <div class="flex flex-col w-16 shrink-0">
                                <span class="text-[12px] font-bold uppercase {lvl.color}">{lvl.label}</span>
                                <span class="text-[11px] font-mono text-text-faint">{lvl.range}</span>
                            </div>
                            <span class="text-[12px] text-text-muted leading-snug">{lvl.meaning}</span>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>
        {/if}

        <!-- MOVE -->
        {#if data.move && data.move.value != null}
        <div class="relative" role="button"
            onmouseenter={() => showTooltip('move')}
            onmouseleave={hideTooltip}
            onfocus={() => showTooltip('move')} onblur={hideTooltip}
            tabindex="0"
        >
            <div class="flex items-center gap-1.5 pill rounded-lg border border-border {MOVE_BG[data.move.level]} cursor-default">
                <span class="text-[11px] font-semibold text-text-muted uppercase tracking-wider">MOVE</span>
                <span class="text-[13px] font-mono font-bold tabular-nums {MOVE_COLORS[data.move.level]}">{data.move.value.toFixed(1)}</span>
                <span class="text-[10px] font-bold uppercase tracking-wide {MOVE_COLORS[data.move.level]}">{data.move.level}</span>
            </div>
            {#if activeTooltip === 'move'}
                <div class="tt-card" role="tooltip" onmouseenter={keepTooltip} onmouseleave={hideTooltip}>
                    <div class="text-[13px] font-semibold text-text uppercase tracking-wider mb-1">{TOOLTIPS.move.title}</div>
                    <div class="text-[12px] text-text-muted leading-relaxed mb-2">{TOOLTIPS.move.description}</div>
                    {#each TOOLTIPS.move.levels as lvl}
                        <div class="flex items-start gap-2 mb-1.5">
                            <div class="flex flex-col w-16 shrink-0">
                                <span class="text-[12px] font-bold uppercase {lvl.color}">{lvl.label}</span>
                                <span class="text-[11px] font-mono text-text-faint">{lvl.range}</span>
                            </div>
                            <span class="text-[12px] text-text-muted leading-snug">{lvl.meaning}</span>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>
        {/if}

        <!-- Yield Curve -->
        <div class="relative" role="button"
            onmouseenter={() => showTooltip('curve')}
            onmouseleave={hideTooltip}
            onfocus={() => showTooltip('curve')} onblur={hideTooltip}
            tabindex="0"
        >
            <div class="flex items-center gap-1.5 pill rounded-lg border border-border {CURVE_BG[data.yield_curve.status]} cursor-default">
                <span class="text-[11px] font-semibold text-text-muted uppercase tracking-wider">Curve</span>
                <span class="text-[13px] font-mono font-bold tabular-nums {CURVE_COLORS[data.yield_curve.status]}">{data.yield_curve.value != null ? (data.yield_curve.value >= 0 ? '+' : '') + data.yield_curve.value.toFixed(2) + '%' : '—'}</span>
                <span class="text-[10px] font-bold uppercase tracking-wide {CURVE_COLORS[data.yield_curve.status]}">{data.yield_curve.status}</span>
            </div>
            {#if activeTooltip === 'curve'}
                <div class="tt-card" role="tooltip" onmouseenter={keepTooltip} onmouseleave={hideTooltip}>
                    <div class="text-[13px] font-semibold text-text uppercase tracking-wider mb-1">{TOOLTIPS.curve.title}</div>
                    <div class="text-[12px] text-text-muted leading-relaxed mb-2">{TOOLTIPS.curve.description}</div>
                    {#each TOOLTIPS.curve.levels as lvl}
                        <div class="flex items-start gap-2 mb-1.5">
                            <div class="flex flex-col w-20 shrink-0">
                                <span class="text-[12px] font-bold uppercase {lvl.color}">{lvl.label}</span>
                                <span class="text-[11px] font-mono text-text-faint">{lvl.range}</span>
                            </div>
                            <span class="text-[12px] text-text-muted leading-snug">{lvl.meaning}</span>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>

        <!-- Credit -->
        <div class="relative" role="button"
            onmouseenter={() => showTooltip('credit')}
            onmouseleave={hideTooltip}
            onfocus={() => showTooltip('credit')} onblur={hideTooltip}
            tabindex="0"
        >
            <div class="flex items-center gap-1.5 pill rounded-lg border border-border {CREDIT_BG[data.credit.status]} cursor-default">
                <span class="text-[11px] font-semibold text-text-muted uppercase tracking-wider">Credit</span>
                <span class="text-[13px] font-mono font-bold tabular-nums {CREDIT_COLORS[data.credit.status]}">{data.credit.value != null ? data.credit.value.toFixed(2) + '%' : '—'}</span>
                <span class="text-[10px] font-bold uppercase tracking-wide {CREDIT_COLORS[data.credit.status]}">{data.credit.status}</span>
            </div>
            {#if activeTooltip === 'credit'}
                <div class="tt-card" role="tooltip" onmouseenter={keepTooltip} onmouseleave={hideTooltip}>
                    <div class="text-[13px] font-semibold text-text uppercase tracking-wider mb-1">{TOOLTIPS.credit.title}</div>
                    <div class="text-[12px] text-text-muted leading-relaxed mb-2">{TOOLTIPS.credit.description}</div>
                    {#each TOOLTIPS.credit.levels as lvl}
                        <div class="flex items-start gap-2 mb-1.5">
                            <div class="flex flex-col w-20 shrink-0">
                                <span class="text-[12px] font-bold uppercase {lvl.color}">{lvl.label}</span>
                                <span class="text-[11px] font-mono text-text-faint">{lvl.range}</span>
                            </div>
                            <span class="text-[12px] text-text-muted leading-snug">{lvl.meaning}</span>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>

        <!-- USD -->
        <div class="relative" role="button"
            onmouseenter={() => showTooltip('usd')}
            onmouseleave={hideTooltip}
            onfocus={() => showTooltip('usd')} onblur={hideTooltip}
            tabindex="0"
        >
            <div class="flex items-center gap-1.5 pill rounded-lg border border-border {USD_BG[data.usd.status]} cursor-default">
                <span class="text-[11px] font-semibold text-text-muted uppercase tracking-wider">USD</span>
                <span class="text-[13px] font-mono font-bold tabular-nums {USD_COLORS[data.usd.status]}">{data.usd.avg_change >= 0 ? '+' : ''}{data.usd.avg_change.toFixed(2)}%</span>
                <span class="text-[10px] font-bold uppercase tracking-wide {USD_COLORS[data.usd.status]}">{data.usd.status}</span>
            </div>
            {#if activeTooltip === 'usd'}
                <div class="tt-card" role="tooltip" onmouseenter={keepTooltip} onmouseleave={hideTooltip}>
                    <div class="text-[13px] font-semibold text-text uppercase tracking-wider mb-1">{TOOLTIPS.usd.title}</div>
                    <div class="text-[12px] text-text-muted leading-relaxed mb-2">{TOOLTIPS.usd.description}</div>
                    {#each TOOLTIPS.usd.levels as lvl}
                        <div class="flex items-start gap-2 mb-1.5">
                            <div class="flex flex-col w-24 shrink-0">
                                <span class="text-[12px] font-bold uppercase {lvl.color}">{lvl.label}</span>
                                <span class="text-[11px] font-mono text-text-faint">{lvl.range}</span>
                            </div>
                            <span class="text-[12px] text-text-muted leading-snug">{lvl.meaning}</span>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>
    </div>
{:else if loading}
    <div class="flex items-center gap-2">
        <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin" aria-hidden="true"></div>
        <span class="text-[11px] font-medium text-text-faint uppercase tracking-wider" aria-live="polite">Loading risk signals...</span>
    </div>
{/if}

<style>
    .risk-dash-root { container-type: inline-size; }

    .pill {
        padding: 4px 10px;
        white-space: nowrap;
    }
    .pill span { font-size: 11px; }
    .pill span:first-child { font-size: 10px; }

    @container (max-width: 600px) {
        .pill { padding: 3px 6px; gap: 3px; }
        .pill span { font-size: 10px; }
        .pill span:first-child { font-size: 9px; }
    }

    @container (max-width: 420px) {
        .pill { padding: 2px 4px; gap: 2px; }
        .pill span { font-size: 9px; }
        .pill span:first-child { font-size: 8px; }
        .pill span:last-child { display: none; }
    }

    .tt-card {
        position: absolute;
        top: calc(100% + 8px);
        left: 0;
        z-index: 50;
        width: min(400px, calc(100vw - 2rem));
        padding: var(--space-md) var(--space-lg);
        background: var(--surface-overlay);
        border: 1px solid var(--border-default);
        border-radius: var(--radius-lg);
        backdrop-filter: blur(8px);
        box-shadow: var(--shadow-tooltip);
        animation: tt-in 0.15s ease-out;
    }

    /* Center tooltip on small viewports to prevent overflow */
    @media (max-width: 768px) {
        .tt-card {
            left: 50%;
            transform: translateX(-50%);
        }
    }

    @keyframes tt-in {
        from { opacity: 0; transform: translateY(-4px); }
        to { opacity: 1; transform: translateY(0); }
    }

    @media (max-width: 768px) {
        @keyframes tt-in {
            from { opacity: 0; transform: translateX(-50%) translateY(-4px); }
            to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
    }
</style>
