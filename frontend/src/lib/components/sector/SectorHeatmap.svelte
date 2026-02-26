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
    import { sectorSelectedIndices, selectedSector } from '$lib/stores.js';
    import { getSectorColor, SECTOR_ABBREV } from '$lib/theme.js';
    import { INDEX_CONFIG, INDEX_COLORS } from '$lib/index-registry.js';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { fmtDate } from '$lib/format.js';

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
        const a = (0.55 + t * 0.45).toFixed(2);
        return `color:var(--text-primary); opacity:${a}`;
    }
    function avgTextColor(val) {
        if (val == null) return 'color:var(--text-disabled)';
        return val >= 0 ? 'color:var(--color-positive)' : 'color:var(--color-negative)';
    }
</script>

<Card fill padding={false} class="heatmap-root bg-bg-hover min-h-0 max-lg:overflow-x-auto">

    <!-- header -->
    <div class="px-5 pt-5 pb-3 flex-shrink-0 border-b border-border">
        <SectionHeader title="Sector Heatmap" subtitle="Normalized % Change">
            {#snippet action()}
                <span class="text-[10px] font-semibold text-accent uppercase tracking-wider">{periodLabel}</span>
                {#if !allSectorSeries}
                    <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin"></div>
                {/if}
            {/snippet}
        </SectionHeader>
    </div>

    {#if sorted.length === 0 && !allSectorSeries}
        <div class="flex-1 flex items-center justify-center">
            <div class="w-4 h-4 border border-border border-t-text-muted rounded-full animate-spin"></div>
        </div>
    {:else}
    <div class="flex-1 min-h-0 p-3 flex flex-col">
        <!-- column headers -->
        <div class="flex shrink-0 col-hdr-row" style="padding-left: 18%;">
            <div class="flex-shrink-0 flex items-center justify-center col-avg-hdr">
                <span class="col-hdr-text font-semibold text-text-muted uppercase tracking-wider">Avg</span>
            </div>
            {#each indices as idx}
                <div class="flex-1 flex items-center justify-center gap-1" title="{INDEX_CONFIG[idx]?.label||idx}">
                    <span class="{INDEX_CONFIG[idx]?.flag || ''} fis rounded-sm flex-shrink-0 col-flag"></span>
                    <span class="col-hdr-text font-semibold uppercase tracking-wider truncate"
                          style="color:{INDEX_COLORS[idx]||'#8b5cf6'}">{INDEX_CONFIG[idx]?.abbr||idx}</span>
                </div>
            {/each}
        </div>

        <!-- matrix rows -->
        <div class="flex-1 flex flex-col min-h-0" role="grid" aria-label="Sector return heatmap">
            {#each sorted as row, rowIdx (row.sector)}
                {@const isActive = row.sector === activeSec}
                {@const dimmed = activeSec && !isActive}
                <div class="flex-1 flex items-center min-h-0 row-wrap" role="row">
                    <!-- row header (sector name) -->
                    <button class="w-[18%] flex items-center pr-2 shrink-0 h-full cursor-pointer transition-opacity"
                         style="opacity: {dimmed ? 0.25 : 1};"
                         onclick={() => selectedSector.set(row.sector)}>
                        <span class="row-sec font-bold truncate uppercase tracking-wider"
                              style="color:{isActive ? getSectorColor(row.sector) : 'var(--text-secondary)'}; {isActive ? 'text-decoration: underline; text-underline-offset: 4px; text-decoration-thickness: 2px;' : ''}">
                            {SECTOR_ABBREV[row.sector] || row.sector}
                        </span>
                    </button>

                    <!-- average return -->
                    <div class="flex-shrink-0 flex items-center justify-center cell-avg transition-opacity"
                         style="opacity: {dimmed ? 0.2 : 1};">
                        <span class="cell-text font-semibold tabular-nums"
                              style="{avgTextColor(row.avg_return_pct)}">
                            {fmt(row.avg_return_pct)}
                        </span>
                    </div>

                    <!-- per-index cells (same tile style as correlation matrix) -->
                    {#each indices as idx}
                        {@const val = row.indices?.[idx]?.return_pct ?? null}
                        <button
                            role="gridcell"
                            class="flex-1 flex items-center justify-center rounded-sm cell-inner transition-all
                                cursor-pointer hover:ring-1 hover:ring-border hover:scale-[1.03]"
                            style="{cellBg(val)}; {cellTextColor(val)}; {dimmed ? 'opacity: 0.2;' : ''}"
                            title="{INDEX_CONFIG[idx]?.label||idx}: {fmt(val)}"
                            onclick={() => selectedSector.set(row.sector)}
                        >
                            <span class="cell-text tabular-nums font-medium">
                                {useShort ? fmtShort(val) : fmt(val)}
                            </span>
                        </button>
                    {/each}
                </div>
            {/each}
        </div>
    </div>
    {/if}
</Card>

<style>
    /* container queries drive responsive sizing via cqh units */
    :global(.heatmap-root) { container-type: size; }

    .col-hdr-row  { padding-top: clamp(2px, 0.5cqh, 5px); padding-bottom: clamp(2px, 0.5cqh, 5px); }
    .col-hdr-text { font-size: clamp(10px, 1.3cqh, 14px); }
    .col-flag     { font-size: clamp(9px, 1.1cqh, 13px) !important; }
    .col-avg-hdr  { width: clamp(44px, 7cqw, 64px); }

    .row-wrap     { min-height: 0; }
    .row-sec      { font-size: clamp(10px, 1.3cqh, 14px); }
    .cell-avg     { width: clamp(44px, 7cqw, 64px); }
    .cell-text    { font-size: clamp(9px, 1.2cqh, var(--text-num-md)); }

    /* Match correlation matrix tile style: full height, vertical gaps only */
    .cell-inner   { min-height: 0; height: 100%; margin-top: 1px; margin-bottom: 1px; }
</style>