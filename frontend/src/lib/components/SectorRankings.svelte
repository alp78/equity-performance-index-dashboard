<!--
  SectorRankings.svelte
  Single-index mode: all sectors ranked by return as horizontal bars.
  Colour per sector matches chart lines. Click row → selects sector.
-->
<script>
    import { browser } from '$app/environment';
    import { API_BASE_URL } from '$lib/config.js';
    import { singleSelectedIndex, selectedSector, selectedSectors, INDEX_CONFIG } from '$lib/stores.js';

    let { currentPeriod = '1y', customRange = null, industryReturnOverride = null } = $props();

    const ALL_SECTORS = [
        'Technology', 'Financial Services', 'Healthcare', 'Industrials',
        'Consumer Cyclical', 'Communication Services', 'Consumer Defensive',
        'Energy', 'Basic Materials', 'Utilities', 'Real Estate',
    ];
    const SECTOR_PALETTE = [
        '#8b5cf6','#06b6d4','#f59e0b','#ef4444','#22c55e',
        '#ec4899','#3b82f6','#f97316','#84cc16','#a855f7','#14b8a6',
    ];
    function sectorColor(sec) {
        const i = ALL_SECTORS.indexOf(sec);
        return SECTOR_PALETTE[(i >= 0 ? i : ALL_SECTORS.length) % SECTOR_PALETTE.length];
    }

    let rows     = $state([]);
    let loading  = $state(false);
    let cache    = {};

    let indexKey  = $derived(($singleSelectedIndex || [])[0] || 'sp500');
    let activeSec = $derived($selectedSector);
    let indexCfg  = $derived(INDEX_CONFIG?.[indexKey] || {});

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

    async function load(period, range, idx) {
        if (!browser || !idx) return;
        const pKey = range?.start ? `${range.start}_${range.end}` : (period || '1y');
        const cKey = `rankings_${idx}_${pKey}`;
        if (cache[cKey]) { rows = cache[cKey]; return; }
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
                // If the currently selected sector doesn't exist in this index, reset to top
                const sectorNames = new Set(mapped.map(r => r.sector));
                if (mapped.length > 0 && !sectorNames.has($selectedSector)) {
                    selectedSector.set(mapped[0].sector);
                }
            }
        } catch {}
        loading = false;
    }

    $effect(() => { load(currentPeriod, customRange, indexKey); });

    // Filter to selected sectors only, apply industry overrides, re-sort
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

    // If active sector is deselected, fall back to first in filtered list
    $effect(() => {
        if (filteredRows.length > 0 && !filteredRows.some(r => r.sector === $selectedSector)) {
            selectedSector.set(filteredRows[0].sector);
        }
    });

    let maxAbs = $derived(Math.max(...filteredRows.map(r => Math.abs(r.return_pct || 0)), 1));
    function barW(val) { return `${Math.min(Math.abs(val) / maxAbs * 100, 100).toFixed(1)}%`; }
</script>

<div class="rankings-root h-full w-full flex flex-col bg-white/[0.03] rounded-2xl border border-white/5 overflow-hidden min-h-0">

    <div class="flex items-start justify-between px-5 pt-5 pb-3 flex-shrink-0 border-b border-white/5">
        <div class="flex flex-col items-start">
            <div class="flex items-center gap-2">
                <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">Sector Rankings</h3>
                <span class="text-[9px] font-black text-orange-400 uppercase tracking-wider">{periodLabel}</span>
            </div>
            <span class="text-[11px] font-black uppercase tracking-wider mt-1"
                  style="color:{indexCfg.color || '#8b5cf6'}">
                {indexCfg.shortLabel || indexKey}
            </span>
        </div>
        {#if loading}
            <div class="w-3 h-3 border border-white/10 border-t-white/40 rounded-full animate-spin mt-1 flex-shrink-0"></div>
        {/if}
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden flex flex-col justify-start px-4 py-2 gap-2">
        {#if rows.length === 0 && !loading}
            <div class="flex items-center justify-center h-full text-white/15 text-[10px] font-bold uppercase tracking-widest">No data</div>
        {/if}
        {#each filteredRows as row (row.sector)}
            {@const isActive = row.sector === activeSec}
            {@const color = sectorColor(row.sector)}
            {@const pos = (row.return_pct ?? 0) >= 0}
            <button
                class="row-item w-full flex items-center gap-3 rounded-lg px-2 transition-all duration-150
                    {isActive ? 'bg-white/[0.04]' : 'hover:bg-white/[0.02]'}"
                onclick={() => selectedSector.set(row.sector)}
            >
                <div class="w-1.5 h-1.5 rounded-full flex-shrink-0 transition-all duration-150"
                     style="background:{isActive ? color : 'rgba(255,255,255,0.15)'}; {row._filtered ? 'box-shadow: 0 0 4px rgba(249,115,22,0.6)' : ''}"></div>
                <span class="sector-name font-bold flex-shrink-0 truncate text-left transition-colors duration-150"
                      style="width:38%; color:{isActive ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,0.45)'}">
                    {row.sector}
                </span>
                <div class="flex-1 flex items-center gap-2 min-w-0">
                    <div class="flex-1 h-[9px] rounded-full bg-white/[0.05] overflow-hidden min-w-0">
                        <div class="h-full rounded-full transition-all duration-500"
                             style="width:{barW(row.return_pct)}; background:{pos ? color : '#ef4444'}; opacity:{isActive ? 1 : 0.65}">
                        </div>
                    </div>
                    <span class="return-val font-black font-mono tabular-nums flex-shrink-0"
                          style="color:{pos ? 'rgba(34,197,94,0.9)' : 'rgba(239,68,68,0.85)'}">
                        {fmt(row.return_pct)}
                    </span>
                </div>
                <span class="stock-ct font-bold tabular-nums flex-shrink-0 text-white/20">{row.stock_count}</span>
            </button>
        {/each}
    </div>
</div>

<style>
    .rankings-root { container-type: size; }
    .row-item    { min-height: 22px; max-height: 46px; flex: 1; }
    .sector-name { font-size: 13px; }
    .return-val  { font-size: 13px; font-weight: 900; min-width: 58px; text-align: right; }
    .stock-ct    { font-size: 12px; min-width: 24px; text-align: right; }
</style>