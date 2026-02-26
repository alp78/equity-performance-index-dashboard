<!--
  ═══════════════════════════════════════════════════════════════════════════
   SectorRankings — Single-Index Sector Bar Chart
  ═══════════════════════════════════════════════════════════════════════════
   Ranked horizontal bar chart showing each sector's return % for the
   currently selected index.  Clicking a row sets selectedSector (orange
   highlight) which drives the SectorHeatmap and SectorTopStocks panels.
   Supports industry-level return overrides passed from parent via prop.

   Data source : GET /sector-comparison/table?indices={idx}
   Placement   : sidebar "Sector Rotation" → single-index mode
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { browser } from '$app/environment';
    import { API_BASE_URL } from '$lib/config.js';
    import { singleSelectedIndex, selectedSector, selectedSectors, sectorHighlightEnabled, INDEX_CONFIG } from '$lib/stores.js';
    import { SECTOR_PALETTE } from '$lib/theme.js';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';

    let { currentPeriod = '1y', customRange = null, industryReturnOverride = null } = $props();

    // --- SECTOR PALETTE ---

    const ALL_SECTORS = [
        'Information Technology', 'Financials', 'Healthcare', 'Industrials',
        'Consumer Discretionary', 'Communication Services', 'Consumer Staples',
        'Energy', 'Materials', 'Utilities', 'Real Estate',
    ];
    function sectorColor(sec) {
        const i = ALL_SECTORS.indexOf(sec);
        return SECTOR_PALETTE[(i >= 0 ? i : ALL_SECTORS.length) % SECTOR_PALETTE.length];
    }

    // --- STATE ---

    let rows     = $state([]);
    let loading  = $state(false);
    let hasEverLoaded = $state(false);
    let cache    = {};

    let indexKey  = $derived(($singleSelectedIndex || [])[0] || 'sp500');
    let activeSec = $derived($selectedSector);
    let indexCfg  = $derived(INDEX_CONFIG?.[indexKey] || {});

    // --- PERIOD LABEL ---

    function fmtDate(d) {
        if (!d) return '';
        const dt = new Date(d + 'T00:00:00');
        return `${dt.getDate()} ${dt.toLocaleDateString('en-GB',{month:'short'})} '${String(dt.getFullYear()).slice(2)}`;
    }
    let isCustom    = $derived(!!(customRange?.start));
    let periodLabel = $derived(
        isCustom ? `${fmtDate(customRange.start)} → ${fmtDate(customRange.end)}`
                 : (currentPeriod || '1y').toUpperCase()
    );

    function fmt(v) {
        if (v == null) return '—';
        return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
    }

    // --- DATA LOADING ---

    async function load(period, range, idx) {
        if (!browser || !idx) return;
        const pKey = range?.start ? `${range.start}_${range.end}` : (period || '1y');
        const cKey = `rankings_${idx}_${pKey}`;
        if (cache[cKey]) { rows = cache[cKey]; hasEverLoaded = true; return; }
        loading = true;
        try {
            const url = range?.start
                ? `${API_BASE_URL}/sector-comparison/table?indices=${idx}&start=${range.start}&end=${range.end}`
                : `${API_BASE_URL}/sector-comparison/table?indices=${idx}&period=${period||'1y'}`;
            const ctrl = new AbortController();
            const t = setTimeout(() => ctrl.abort(), 12000);
            const res = await fetch(url, { signal: ctrl.signal });
            clearTimeout(t);
            if (res.ok) {
                const data = await res.json();
                const mapped = data.map(row => ({
                    sector:      row.sector,
                    return_pct:  row.indices?.[idx]?.return_pct ?? row.avg_return_pct ?? null,
                    stock_count: row.indices?.[idx]?.stock_count ?? 0,
                })).filter(r => r.return_pct != null)
                   .sort((a, b) => b.return_pct - a.return_pct);
                cache[cKey] = mapped;
                rows = mapped;
                // reset to top sector if current selection missing from this index
                const sectorNames = new Set(mapped.map(r => r.sector));
                if (mapped.length > 0 && !sectorNames.has($selectedSector)) {
                    selectedSector.set(mapped[0].sector);
                }
            }
        } catch {}
        loading = false;
        hasEverLoaded = true;
    }

    $effect(() => { load(currentPeriod, customRange, indexKey); });

    // --- FILTERING AND OVERRIDES ---
    // apply sidebar sector filter + industry-level return overrides, re-sort
    let filteredRows = $derived((() => {
        let base = $selectedSectors && $selectedSectors.length > 0
            ? rows.filter(r => $selectedSectors.includes(r.sector))
            : rows;
        if (!industryReturnOverride) return base;
        return base.map(row => {
            if (industryReturnOverride[row.sector] !== undefined) {
                return { ...row, return_pct: industryReturnOverride[row.sector], _filtered: true };
            }
            return row;
        }).sort((a, b) => (b.return_pct ?? 0) - (a.return_pct ?? 0));
    })());

    // fall back to first row if active sector was filtered out
    $effect(() => {
        if (filteredRows.length > 0 && !filteredRows.some(r => r.sector === $selectedSector)) {
            selectedSector.set(filteredRows[0].sector);
        }
    });

    let maxAbs = $derived(Math.max(...filteredRows.map(r => Math.abs(r.return_pct || 0)), 1));
    function barW(val) { return `${Math.min(Math.abs(val) / maxAbs * 100, 100).toFixed(1)}%`; }
</script>

<Card fill padding={false} class="rankings-root min-h-0">

    <!-- header -->
    <div class="px-5 pt-5 pb-3 flex-shrink-0">
        <SectionHeader title="Sector Rankings" subtitle="Normalized % Change" border>
            {#snippet action()}
                <span class="text-[10px] font-semibold text-accent uppercase tracking-wider">{periodLabel}</span>
                {#if loading}
                    <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin flex-shrink-0"></div>
                {/if}
            {/snippet}
        </SectionHeader>
    </div>

    <!-- ranked sector rows -->
    <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden flex flex-col justify-start px-4 py-2 gap-2">
        {#if rows.length === 0 && !loading && !hasEverLoaded}
            <div class="flex items-center justify-center h-full">
                <div class="w-4 h-4 border border-border border-t-text-muted rounded-full animate-spin"></div>
            </div>
        {:else if rows.length === 0 && !loading && hasEverLoaded}
            <div class="flex items-center justify-center h-full text-text-muted text-[11px] font-medium uppercase tracking-widest">No data</div>
        {/if}
        {#each filteredRows as row (row.sector)}
            {@const isActive = row.sector === activeSec}
            {@const color = sectorColor(row.sector)}
            {@const pos = (row.return_pct ?? 0) >= 0}
            <button
                class="row-item w-full flex items-center gap-3 rounded-lg px-2 transition-all duration-150 relative
                    {isActive ? 'bg-accent/10' : 'hover:bg-bg-hover'}"
                onclick={() => { if (row.sector === activeSec) { sectorHighlightEnabled.update(v => !v); return; } sectorHighlightEnabled.set(true); selectedSector.set(row.sector); }}
            >
                {#if isActive}
                    <div class="absolute left-0 top-0 bottom-0 w-[3px] rounded-l-lg" style="background:{color}"></div>
                {/if}
                <span class="sector-name font-bold flex-shrink-0 truncate text-left transition-colors duration-150"
                      style="width:40%; color:{isActive ? color : 'var(--color-text-secondary)'}">
                    {row.sector}
                </span>
                <div class="flex-1 flex items-center gap-2 min-w-0">
                    <div class="flex-1 h-[9px] rounded-full bg-bg-hover overflow-hidden min-w-0">
                        <div class="h-full rounded-full transition-all duration-500"
                             style="width:{barW(row.return_pct)}; background:var(--color-text-muted); opacity:{isActive ? 1 : 0.6}">
                        </div>
                    </div>
                    <span class="return-val font-semibold tabular-nums flex-shrink-0"
                          style="color:{pos ? 'var(--color-up)' : 'var(--color-down)'}">
                        {fmt(row.return_pct)}
                    </span>
                </div>
                <span class="stock-ct font-medium tabular-nums flex-shrink-0 text-text-faint">{row.stock_count}</span>
            </button>
        {/each}
    </div>
</Card>

<style>
    :global(.rankings-root) { container-type: size; }
    .row-item    { min-height: 22px; max-height: 46px; flex: 1; }
    .sector-name { font-size: 14px; }
    .return-val  { font-size: 14px; font-weight: 600; min-width: 58px; text-align: right; }
    .stock-ct    { font-size: 13px; min-width: 24px; text-align: right; }

    /* Container query: narrow width — shrink text, tighten layout */
    @container (max-width: 320px) {
        .sector-name { font-size: 12px; width: 35% !important; }
        .return-val  { font-size: 12px; min-width: 48px; }
        .stock-ct    { font-size: 11px; min-width: 20px; }
    }

    /* Very narrow container */
    @container (max-width: 240px) {
        .sector-name { font-size: 11px; width: 30% !important; }
        .return-val  { font-size: 11px; min-width: 42px; }
        .stock-ct    { display: none; }
    }

    /* Viewport fallback for browsers without container query support */
    @media (max-width: 640px) {
        .sector-name { font-size: 12px; }
        .return-val  { font-size: 12px; min-width: 48px; }
        .stock-ct    { font-size: 11px; min-width: 20px; }
    }
</style>