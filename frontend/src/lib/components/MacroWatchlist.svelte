<!--
  ═══════════════════════════════════════════════════════════════════════════
   MacroWatchlist — Bond Yields, Commodities, VIX & FX Rates
  ═══════════════════════════════════════════════════════════════════════════
   Real-time macro instrument watchlist fed by FRED (bonds, commodities,
   rates, volatility) and Frankfurter (FX pairs).  Responds to the
   riskHighlight store — when the RiskDashboard pill is hovered, this
   component scrolls to and highlights the matching FRED series or FX row.

   Data source : GET /macro/rates  (FRED + yfinance, 5–60 min cache)
                 GET /macro/fx     (Frankfurter ECB data, 5 min cache)
   Placement   : sidebar "Global Macro" mode, right column
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { onMount, onDestroy } from 'svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { riskHighlight, getCached, setCached, isCacheFresh } from '$lib/stores.js';

    // Map risk pill keys to FRED series IDs or 'fx' for the FX section
    const RISK_TO_IDS = {
        vix: ['VIXCLS'],
        eu_vol: ['EU_VOL'],
        move: ['MOVE'],
        curve: ['T10Y2Y'],
        credit: ['BAMLH0A0HYM2'],
        usd: 'fx',
    };

    let highlight = $state(null);
    const unsub = riskHighlight.subscribe(v => { highlight = v; });

    function isHighlightedInst(inst) {
        if (!highlight) return false;
        const ids = RISK_TO_IDS[highlight];
        if (!ids || ids === 'fx') return false;
        return ids.includes(inst.id);
    }

    function isHighlightedFx() {
        return highlight === 'usd';
    }

    let fxRates = $state({});
    let macroRates = $state([]);
    let fxLoading = $state(true);
    let ratesLoading = $state(true);
    let fxTimer;
    let ratesTimer;

    const FX_PAIRS = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CNY', 'USD/INR', 'USD/CHF', 'AUD/USD'];

    const FX_FLAGS = {
        'EUR/USD': 'fi-eu', 'GBP/USD': 'fi-gb', 'USD/JPY': 'fi-jp',
        'USD/CNY': 'fi-cn', 'USD/INR': 'fi-in', 'USD/CHF': 'fi-ch', 'AUD/USD': 'fi-au',
    };

    const GROUP_ORDER = ['bonds', 'commodities', 'rates', 'volatility'];
    const GROUP_LABELS = { bonds: 'BONDS', commodities: 'COMMODITIES', rates: 'RATES', volatility: 'VOLATILITY' };

    let groupedRates = $derived.by(() => {
        const groups = {};
        for (const g of GROUP_ORDER) groups[g] = [];
        for (const inst of macroRates) {
            if (groups[inst.group]) groups[inst.group].push(inst);
        }
        return groups;
    });

    const FX_TTL = 5 * 60 * 1000;
    const RATES_TTL = 60 * 60 * 1000;

    async function loadFx() {
        try {
            const res = await fetch(`${API_BASE_URL}/macro/fx`);
            if (res.ok) {
                const d = await res.json();
                fxRates = d.rates || {};
                setCached('macro_fx', fxRates, FX_TTL);
            }
        } catch {}
        fxLoading = false;
    }

    async function loadRates() {
        try {
            const res = await fetch(`${API_BASE_URL}/macro/rates`);
            if (res.ok) {
                const d = await res.json();
                macroRates = d.instruments || [];
                setCached('macro_rates', macroRates, RATES_TTL);
            }
        } catch {}
        ratesLoading = false;
    }

    onMount(() => {
        // Serve cached data instantly, refresh in background if stale
        const fxCached = getCached('macro_fx');
        if (fxCached) { fxRates = fxCached.data; fxLoading = false; }
        const ratesCached = getCached('macro_rates');
        if (ratesCached) { macroRates = ratesCached.data; ratesLoading = false; }

        if (!isCacheFresh('macro_fx')) loadFx();
        if (!isCacheFresh('macro_rates')) loadRates();

        fxTimer = setInterval(loadFx, FX_TTL);
        ratesTimer = setInterval(loadRates, RATES_TTL);
    });

    onDestroy(() => {
        if (fxTimer) clearInterval(fxTimer);
        if (ratesTimer) clearInterval(ratesTimer);
        unsub();
    });

    function fmtVal(val, unit) {
        if (val == null) return '—';
        if (unit === '%') return val.toFixed(2) + '%';
        if (unit === '$/oz' || unit === '$/bbl' || unit === '$/MMBtu' || unit === '$/sh') return '$' + val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        return val.toFixed(2);
    }

    function fmtChange(change, unit) {
        if (change == null) return '';
        const sign = change >= 0 ? '+' : '';
        if (unit === '%') return sign + change.toFixed(2) + 'bp';
        if (unit === '$/oz' || unit === '$/bbl' || unit === '$/MMBtu' || unit === '$/sh') return sign + '$' + Math.abs(change).toFixed(2);
        return sign + change.toFixed(2);
    }

    function fmtFx(pair, val) {
        if (val == null) return '—';
        if (pair.startsWith('USD/')) return val.toFixed(pair === 'USD/JPY' ? 2 : 4);
        return val.toFixed(4);
    }
</script>

<div class="macro-watchlist-root flex flex-col h-full">
    {#if ratesLoading && fxLoading}
        <div class="flex-1 flex flex-col items-center justify-center opacity-40">
            <div class="w-6 h-6 border-2 border-border border-t-text-muted rounded-full animate-spin"></div>
            <span class="text-[12px] font-semibold uppercase tracking-widest text-text-muted mt-3">Loading Macro</span>
        </div>
    {:else}
        <div class="flex-1 flex flex-col min-h-0">
            <!-- FX Rates -->
            {#if Object.keys(fxRates).length > 0}
                <div class="flex-1 flex items-center gap-2 px-3 py-1.5 bg-surface-2 border-b border-border-subtle min-h-0">
                    <span class="text-[11px] font-bold text-text-secondary uppercase tracking-widest">FX RATES</span>
                </div>
                {#each FX_PAIRS as pair}
                    {@const fx = fxRates[pair]}
                    {#if fx}
                        {@const val = typeof fx === 'object' ? fx.value : fx}
                        {@const pct = typeof fx === 'object' ? fx.changePct : null}
                        <div class="flex-1 flex items-center gap-2 pr-3 border-b border-border-subtle hover:bg-bg-hover transition-all min-h-0 relative
                            {isHighlightedFx() ? 'ml-4 pl-3 bg-bg-selected !border-b-selected-border/50 rounded-l-md' : 'pl-4'}">
                            {#if isHighlightedFx()}<div class="absolute left-0 top-0 bottom-0 w-1 rounded-l-md bg-selected-border"></div>{/if}
                            <span class="fi {FX_FLAGS[pair]} fis rounded-sm shrink-0" style="font-size: 0.9rem;"></span>
                            <div class="flex-1 text-left min-w-0">
                                <span class="inst-name font-medium text-text-muted">{pair}</span>
                            </div>
                            <div class="flex items-baseline gap-1.5 shrink-0">
                                <span class="inst-val font-mono font-semibold text-text-secondary tabular-nums">{fmtFx(pair, val)}</span>
                                {#if pct != null}
                                    <span class="inst-chg font-mono font-semibold tabular-nums {pct >= 0 ? 'text-up' : 'text-down'}">
                                        {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
                                    </span>
                                {/if}
                            </div>
                        </div>
                    {/if}
                {/each}
            {/if}

            <!-- macro instruments grouped -->
            {#each GROUP_ORDER as group}
                {#if groupedRates[group] && groupedRates[group].length > 0}
                    <div class="flex-1 flex items-center gap-2 px-3 py-1.5 bg-surface-2 border-b border-border-subtle min-h-0">
                        <span class="text-[11px] font-bold text-text-secondary uppercase tracking-widest">{GROUP_LABELS[group]}</span>
                    </div>
                    {#each groupedRates[group] as inst}
                        <div class="flex-1 flex items-center gap-2 pr-3 border-b border-border-subtle hover:bg-bg-hover transition-all min-h-0 relative
                            {isHighlightedInst(inst) ? 'ml-4 pl-5 bg-bg-selected !border-b-selected-border/50 rounded-l-md' : 'pl-6'}">{#if isHighlightedInst(inst)}<div class="absolute left-0 top-0 bottom-0 w-1 rounded-l-md bg-selected-border"></div>{/if}
                            <div class="flex-1 text-left min-w-0 flex items-baseline gap-1.5 overflow-hidden">
                                <span class="inst-name font-medium text-text-muted truncate">{inst.name}</span>
                                {#if inst.date}
                                    <span class="inst-date text-text-muted font-medium shrink-0">{inst.date}</span>
                                {/if}
                            </div>
                            <div class="flex items-baseline gap-1.5 shrink-0">
                                <span class="inst-val font-mono font-semibold text-text-secondary tabular-nums">
                                    {fmtVal(inst.value, inst.unit)}
                                </span>
                                {#if inst.change != null}
                                    <span class="inst-chg font-mono font-semibold tabular-nums {inst.change >= 0 ? 'text-up' : 'text-down'}">
                                        {inst.change >= 0 ? '+' : ''}{fmtChange(inst.change, inst.unit)}
                                    </span>
                                {/if}
                            </div>
                        </div>
                    {/each}
                {/if}
            {/each}

        </div>
    {/if}
</div>

<style>
    .macro-watchlist-root { container-type: inline-size; }

    /* Base sizes */
    .inst-name { font-size: 12px; }
    .inst-date { font-size: 9px; }
    .inst-val  { font-size: 12px; }
    .inst-chg  { font-size: 11px; }

    /* Narrow container: tighten text */
    @container (max-width: 280px) {
        .inst-name { font-size: 11px; }
        .inst-val  { font-size: 11px; }
        .inst-chg  { font-size: 10px; }
        .inst-date { font-size: 8px; }
    }

    /* Very narrow container */
    @container (max-width: 220px) {
        .inst-name { font-size: 10px; }
        .inst-val  { font-size: 10px; }
        .inst-chg  { font-size: 9px; }
        .inst-date { display: none; }
    }
</style>
