<!--
  ═══════════════════════════════════════════════════════════════════════════
   SectorHeatmap — Cross-Index Sector Return Grid
  ═══════════════════════════════════════════════════════════════════════════
   Colour-coded grid of sector returns across selected indices.  Each
   cell is green (positive) or red (negative) with intensity capped at
   the 80th percentile to prevent outlier washout.  Clicking a row sets
   selectedSector (orange highlight) which drives SectorTopStocks and the
   comparison chart.  Supports industry filtering (weighted by stock count)
   and precomputed allSectorSeries / industrySeriesCache for fast path.

   Data source : GET /sector-comparison/table?indices=...&period=...
                 allSectorSeries prop (preloaded /sector-comparison/all-series)
   Placement   : sidebar "Sector Rotation" → cross-index mode, right col
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { sectorSelectedIndices, selectedSector, INDEX_CONFIG } from '$lib/stores.js';
    import { INDEX_COLORS, getSectorColor } from '$lib/theme.js';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';

    let {
        currentPeriod = '1y',
        customRange = null,
        industryFilter = null,
        filteredSector = null,
        allSectorSeries = null,
        industrySeriesCache = null,
    } = $props();

    let indices   = $derived($sectorSelectedIndices);
    let activeSec = $derived($selectedSector);

    // Use INDEX_CONFIG[key].flag / .abbr / .label from stores.js

    // --- PERIOD HELPERS ---

    const PERIOD_DAYS = { '1w': 7, '1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '5y': 1825 };

    function fmtDate(d) {
        if (!d) return '';
        const dt = new Date(d + 'T00:00:00');
        return `${dt.getDate()} ${dt.toLocaleDateString('en-GB',{month:'short'})} '${String(dt.getFullYear()).slice(2)}`;
    }
    let isCustom    = $derived(!!(customRange && customRange.start));
    let periodLabel = $derived(
        isCustom ? `${fmtDate(customRange.start)} → ${fmtDate(customRange.end)}`
                 : (currentPeriod || '1y').toUpperCase()
    );

    function getStartDate(period, range) {
        if (range?.start) return range.start;
        if (!period || period.toLowerCase() === 'max') return null;
        const d = PERIOD_DAYS[period.toLowerCase()];
        if (!d) return null;
        const now = new Date();
        now.setDate(now.getDate() - d);
        return now.toISOString().split('T')[0];
    }

    function clipSeries(points, period, range) {
        if (!points || points.length < 2) return null;
        const start = getStartDate(period, range);
        const end = range?.end || null;
        let filtered;
        if (start && end) {
            filtered = points.filter(p => p.time >= start && p.time <= end);
        } else if (start) {
            filtered = points.filter(p => p.time >= start);
        } else {
            filtered = points;
        }
        return filtered && filtered.length >= 2 ? filtered : null;
    }

    // --- VALUE FORMATTERS ---

    function fmt(val) {
        if (val == null) return '—';
        return (val >= 0 ? '+' : '') + val.toFixed(1) + '%';
    }
    // compact format: drops trailing zero for tight cells
    function fmtShort(val) {
        if (val == null) return '—';
        const abs = Math.abs(val);
        const str = abs >= 100 ? abs.toFixed(0) : abs.toFixed(1);
        return (val >= 0 ? '+' : '−') + str + '%';
    }

    // --- DYNAMIC COLUMN WIDTHS ---
    // sector 18%, avg 8%, remaining shared equally among index columns
    let sectorW = $derived('18%');
    let avgW    = $derived('8%');
    let idxW    = $derived(`${Math.floor(74 / Math.max(indices.length, 1))}%`);

    let useShort = $derived(indices.length >= 4);

    // --- DATA FROM PRECOMPUTED SERIES (same source as chart) ---
    // Computes return = lastPct - firstPct over the period for each sector/index.

    let tableData = $derived.by(() => {
        if (!allSectorSeries || indices.length === 0) return [];
        const sectorMap = {};

        for (const idx of indices) {
            const idxData = allSectorSeries[idx];
            if (!idxData) continue;
            for (const [sector, points] of Object.entries(idxData)) {
                const clipped = clipSeries(points, currentPeriod, customRange);
                if (!clipped) continue;
                const returnPct = Math.round((clipped[clipped.length - 1].pct - clipped[0].pct) * 100) / 100;
                if (!sectorMap[sector]) sectorMap[sector] = { sector, indices: {} };
                sectorMap[sector].indices[idx] = { return_pct: returnPct };
            }
        }

        return Object.values(sectorMap).map(row => {
            const returns = Object.values(row.indices).map(v => v.return_pct).filter(v => v != null);
            row.avg_return_pct = returns.length ? Math.round((returns.reduce((a,b)=>a+b,0)/returns.length)*10)/10 : null;
            return row;
        });
    });

    // --- INDUSTRY FILTER OVERLAY (from industry series cache) ---
    // When industries are filtered, compute per-index return from the same
    // precomputed industry series the chart uses (weighted by stock count).

    let filteredOverlay = $derived.by(() => {
        if (!industryFilter || industryFilter.length === 0 || !filteredSector || !industrySeriesCache) return {};
        const sectorCache = industrySeriesCache[filteredSector];
        if (!sectorCache) return {};

        const overlay = {};
        for (const idx of indices) {
            const indMap = sectorCache[idx];
            if (!indMap) continue;

            let totalWeight = 0;
            let weightedSum = 0;

            for (const ind of industryFilter) {
                const series = indMap[ind];
                const clipped = clipSeries(series, currentPeriod, customRange);
                if (!clipped) continue;
                const delta = clipped[clipped.length - 1].pct - clipped[0].pct;
                const weight = clipped[clipped.length - 1].n || 1;
                weightedSum += delta * weight;
                totalWeight += weight;
            }

            if (totalWeight > 0) {
                overlay[idx] = { return_pct: Math.round((weightedSum / totalWeight) * 100) / 100 };
            }
        }
        return overlay;
    });

    // --- DISPLAY DATA (base + overlay) ---
    let displayData = $derived.by(() => {
        if (!filteredSector || !industryFilter || industryFilter.length === 0 || Object.keys(filteredOverlay).length === 0) {
            return tableData;
        }
        return tableData.map(row => {
            if (row.sector !== filteredSector) return row;
            const newIndices = { ...row.indices };
            for (const [idx, vals] of Object.entries(filteredOverlay)) {
                newIndices[idx] = vals;
            }
            const returns = Object.values(newIndices).map(v => v.return_pct).filter(v => v != null);
            return {
                ...row,
                indices: newIndices,
                avg_return_pct: returns.length ? Math.round((returns.reduce((a,b)=>a+b,0)/returns.length)*10)/10 : null,
            };
        });
    });

    // --- SORTING ---
    let sorted = $derived(
        [...displayData].sort((a,b) => (b.avg_return_pct||0) - (a.avg_return_pct||0))
    );

    // --- COLOUR SCALE ---
    // capped at 80th percentile so outliers don't wash out the rest
    let allVals = $derived(
        displayData.flatMap(row =>
            indices.map(idx => row.indices?.[idx]?.return_pct).filter(v => v != null)
        ).sort((a,b) => a - b)
    );
    let scale = $derived(() => {
        if (allVals.length === 0) return 20;
        const p80  = allVals[Math.floor(allVals.length * 0.80)] ?? 20;
        const p20  = allVals[Math.floor(allVals.length * 0.20)] ?? -20;
        return Math.max(Math.abs(p80), Math.abs(p20), 5);
    });

    // grayscale intensity — brightness scales with magnitude
    function cellBg(val) {
        if (val == null) return 'background:var(--surface-1)';
        const s = scale();
        const t = Math.min(Math.abs(val) / s, 1);
        const pct = Math.round(t * 18);
        return `background:color-mix(in srgb, var(--text-primary) ${pct}%, var(--surface-1))`;
    }
    function cellTextColor(val) {
        if (val == null) return 'color:var(--text-disabled)';
        const s = scale();
        const t = Math.min(Math.abs(val) / s, 1);
        const a = (0.6 + t * 0.4).toFixed(2);
        return `color:var(--text-primary); opacity:${a}`;
    }
    function avgTextColor(val) {
        if (val == null) return 'color:var(--text-disabled)';
        return val >= 0 ? 'color:var(--color-positive)' : 'color:var(--color-negative)';
    }
</script>

<Card fill padding={false} class="heatmap-root min-h-0 max-lg:overflow-x-auto">

    <!-- header -->
    <div class="px-5 pt-5 pb-3 flex-shrink-0">
        <SectionHeader title="Sector Heatmap" subtitle="Normalized % Change" border>
            {#snippet action()}
                <span class="text-[10px] font-semibold text-accent uppercase tracking-wider">{periodLabel}</span>
                {#if !allSectorSeries}
                    <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin"></div>
                {/if}
            {/snippet}
        </SectionHeader>
    </div>

    <!-- column headers -->
    <div class="flex items-center col-hdr-row px-3 border-b border-border-subtle flex-shrink-0 heatmap-min-w">
        <div style="width:{sectorW}" class="pl-3 text-left flex-shrink-0">
            <span class="col-hdr-text font-semibold text-text-muted uppercase tracking-widest">Sector</span>
        </div>
        <div style="width:{avgW}" class="text-right pr-2 flex-shrink-0">
            <span class="col-hdr-text font-semibold text-text-muted uppercase tracking-widest">Avg</span>
        </div>
        {#each indices as idx}
            <div style="width:{idxW}" class="text-center flex-shrink-0" title="{INDEX_CONFIG[idx]?.label||idx}">
                <div class="flex items-center justify-center gap-1.5">
                    <span class="{INDEX_CONFIG[idx]?.flag || ''} fis rounded-sm flex-shrink-0 col-flag"></span>
                    <span class="col-hdr-text font-semibold uppercase tracking-wider truncate"
                          style="color:{INDEX_COLORS[idx]||'#8b5cf6'}">{INDEX_CONFIG[idx]?.abbr||idx}</span>
                </div>
            </div>
        {/each}
    </div>

    <!-- data rows -->
    <div class="flex-1 min-h-0 overflow-y-auto overflow-x-auto heatmap-min-w">
        {#if sorted.length === 0 && !allSectorSeries}
            <div class="flex items-center justify-center h-full">
                <div class="w-4 h-4 border border-border border-t-text-muted rounded-full animate-spin"></div>
            </div>
        {/if}

        {#each sorted as row, rowIdx (row.sector)}
            {@const isActive = row.sector === activeSec}
            <!-- inset box-shadow acts as bg colour without creating a stacking context -->
            <div class="row-wrap relative border-b border-border-subtle transition-all duration-150 {rowIdx % 2 === 1 ? 'bg-surface-1' : ''}">

                {#if isActive}
                    <div class="absolute left-0 top-0 bottom-0 w-[3px] pointer-events-none bg-selected-border"></div>
                {/if}

                <div
                    class="data-row w-full flex items-center"
                >
                    <!-- sector name — highlight only on active row -->
                    <button style="width:{sectorW}"
                         class="pl-4 pr-2 text-left flex-shrink-0 h-full flex items-center cursor-pointer hover:bg-bg-hover transition-colors {isActive ? 'bg-bg-active' : ''}"
                         onclick={() => selectedSector.set(row.sector)}>
                        <span class="row-sec font-bold truncate block"
                              style="color:{isActive ? getSectorColor(row.sector) : 'var(--text-secondary)'}">
                            {row.sector}
                        </span>
                    </button>

                    <!-- average return -->
                    <div style="width:{avgW}" class="pr-2 text-right flex-shrink-0">
                        <span class="row-avg font-semibold font-mono tabular-nums"
                              style="{avgTextColor(row.avg_return_pct)}">
                            {fmt(row.avg_return_pct)}
                        </span>
                    </div>

                    <!-- per-index cells -->
                    {#each indices as idx}
                        {@const val = row.indices?.[idx]?.return_pct ?? null}
                        <div style="width:{idxW}" class="px-1 flex-shrink-0" title="{INDEX_CONFIG[idx]?.label||idx}: {fmt(val)}">
                            <div class="cell-inner rounded flex items-center justify-center transition-colors duration-300"
                                 style="{cellBg(val)}">
                                <span class="cell-text font-mono tabular-nums font-medium"
                                      style="{cellTextColor(val)}">
                                    {useShort ? fmtShort(val) : fmt(val)}
                                </span>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        {/each}
    </div>
</Card>

<style>
    /* container queries drive responsive sizing via cqh units */
    :global(.heatmap-root) { container-type: size; }

    .col-hdr-row  { padding: clamp(2px, 0.8cqh, 6px) 0; }
    .col-hdr-text { font-size: clamp(11px, 1.5cqh, 13px); }
    .col-flag     { font-size: clamp(12px, 1.6cqh, 16px) !important; }

    /* row height = available height / 12 (11 sectors + header) */
    .row-wrap    { height: calc((100cqh - clamp(45px, 12cqh, 70px)) / 12);
                   min-height: 22px;
                   max-height: 44px; }

    .data-row    { height: 100%;
                   padding: 0;
                   cursor: pointer;
                   display: flex;
                   align-items: center; }

    .row-sec     { font-size: clamp(11px, 1.7cqh, 15px); }
    .row-avg     { font-size: clamp(12px, 1.7cqh, 15px); }
    .cell-text   { font-size: clamp(11px, 1.6cqh, 14px); }

    .cell-inner  { height: calc((100cqh - clamp(45px, 12cqh, 70px)) / 12 - 6px);
                   min-height: 16px;
                   max-height: 36px; }

    /* On mobile/narrow, ensure table has minimum width for readability with horizontal scroll */
    @media (max-width: 1024px) {
        .heatmap-min-w { min-width: 500px; }
    }

    /* Container width query: shrink text for narrow inline widths */
    @container (max-width: 450px) {
        .row-sec   { font-size: 11px; }
        .row-avg   { font-size: 11px; }
        .cell-text { font-size: 10px; }
        .col-hdr-text { font-size: 10px; }
    }
</style>