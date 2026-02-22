<!-- cross-index heatmap grid showing sector returns; click row to select sector -->

<script>
    import { sectorSelectedIndices, selectedSector } from '$lib/stores.js';

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

    // --- INDEX DISPLAY CONFIG ---

    const INDEX_COLORS = {
        sp500:     '#e2e8f0',
        stoxx50:   '#2563eb',
        ftse100:   '#ec4899',
        nikkei225: '#f59e0b',
        csi300:    '#ef4444',
        nifty50:   '#22c55e',
    };
    const INDEX_FLAGS = {
        sp500:     'fi fi-us',
        stoxx50:   'fi fi-eu',
        ftse100:   'fi fi-gb',
        nikkei225: 'fi fi-jp',
        csi300:    'fi fi-cn',
        nifty50:   'fi fi-in',
    };
    // abbreviated labels for narrow column headers
    const INDEX_ABBR = {
        sp500:     'S&P',
        stoxx50:   'STX',
        ftse100:   'FTSE',
        nikkei225: 'NIK',
        csi300:    'CSI',
        nifty50:   'NFT',
    };
    const INDEX_FULL = {
        sp500:     'S&P 500',
        stoxx50:   'STOXX 50',
        ftse100:   'FTSE 100',
        nikkei225: 'Nikkei 225',
        csi300:    'CSI 300',
        nifty50:   'Nifty 50',
    };

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

    // opaque green/red mixed onto dark base — prevents parent color bleed
    function cellBg(val) {
        if (val == null) return 'background:#1a1a20';
        const s = scale();
        const t = Math.min(Math.abs(val) / s, 1);
        const alpha = 0.07 + t * 0.52;
        const bg = 14;
        if (val >= 0) {
            const r = Math.round(bg + alpha * (34  - bg));
            const g = Math.round(bg + alpha * (197 - bg));
            const b = Math.round(bg + alpha * (94  - bg));
            return `background:rgb(${r},${g},${b})`;
        } else {
            const r = Math.round(bg + alpha * (239 - bg));
            const g = Math.round(bg + alpha * (68  - bg));
            const b = Math.round(bg + alpha * (68  - bg));
            return `background:rgb(${r},${g},${b})`;
        }
    }
    // white text, opacity scales with magnitude
    function cellTextColor(val) {
        if (val == null) return 'color:rgba(255,255,255,0.2)';
        const s = scale();
        const t = Math.min(Math.abs(val) / s, 1);
        const a = (0.6 + t * 0.4).toFixed(2);
        return `color:rgba(255,255,255,${a})`;
    }
    function avgTextColor(val) {
        if (val == null) return 'color:rgba(255,255,255,0.3)';
        return val >= 0 ? 'color:rgba(34,197,94,0.95)' : 'color:rgba(239,68,68,0.95)';
    }
</script>

<div class="heatmap-root h-full w-full flex flex-col bg-white/[0.03] rounded-2xl border border-white/5 overflow-hidden min-h-0">

    <!-- header -->
    <div class="flex items-start justify-between px-5 pt-5 pb-3 flex-shrink-0 border-b border-white/5">
        <div class="flex flex-col items-start">
            <div class="flex items-center gap-2">
                <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">Sector Heatmap</h3>
                <span class="text-[9px] font-black text-orange-400 uppercase tracking-wider">{periodLabel}</span>
            </div>
            <div class="flex items-center gap-1.5 mt-1">
                {#if activeSec}
                    <span class="text-[11px] font-black text-bloom-accent uppercase tracking-wider">{activeSec}</span>
                    <span class="text-[11px] text-white/15">·</span>
                {/if}
                <span class="text-[11px] font-bold text-white/20 uppercase tracking-wider">Normalized % Change</span>
            </div>
        </div>
        {#if !allSectorSeries}
            <div class="w-3 h-3 border border-white/10 border-t-white/40 rounded-full animate-spin mt-1"></div>
        {/if}
    </div>

    <!-- column headers -->
    <div class="flex items-center col-hdr-row px-3 border-b border-white/[0.03] flex-shrink-0">
        <div style="width:{sectorW}" class="pl-3 text-left flex-shrink-0">
            <span class="col-hdr-text font-black text-white/15 uppercase tracking-widest">Sector</span>
        </div>
        <div style="width:{avgW}" class="text-right pr-2 flex-shrink-0">
            <span class="col-hdr-text font-black text-white/15 uppercase tracking-widest">Avg</span>
        </div>
        {#each indices as idx}
            <div style="width:{idxW}" class="text-center flex-shrink-0" title="{INDEX_FULL[idx]||idx}">
                <div class="flex items-center justify-center gap-1.5">
                    <span class="{INDEX_FLAGS[idx] || ''} fis rounded-sm flex-shrink-0 col-flag"></span>
                    <span class="col-hdr-text font-black uppercase tracking-wider truncate"
                          style="color:{INDEX_COLORS[idx]||'#8b5cf6'}">{INDEX_ABBR[idx]||idx}</span>
                </div>
            </div>
        {/each}
    </div>

    <!-- data rows -->
    <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden">
        {#if sorted.length === 0 && !allSectorSeries}
            <div class="flex items-center justify-center h-full text-white/15 text-[11px] font-bold uppercase tracking-widest">
                Loading…
            </div>
        {:else if sorted.length === 0}
            <div class="flex items-center justify-center h-full text-white/15 text-[11px] font-bold uppercase tracking-widest">
                No data available
            </div>
        {/if}

        {#each sorted as row (row.sector)}
            {@const isActive = row.sector === activeSec}
            <!-- inset box-shadow acts as bg colour without creating a stacking context -->
            <div class="row-wrap relative border-b border-white/[0.04] transition-all duration-150">

                {#if isActive}
                    <div class="absolute left-0 top-0 bottom-0 w-[3px] pointer-events-none
                        bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.5)]"></div>
                {/if}

                <button
                    class="data-row w-full flex items-center
                        {isActive ? '' : 'hover:bg-white/[0.02]'}"
                    onclick={() => selectedSector.set(row.sector)}
                >
                    <!-- sector name — orange highlight only on active row -->
                    <div style="width:{sectorW}; {isActive ? 'background:rgba(249,115,22,0.12)' : ''}"
                         class="pl-4 pr-2 text-left flex-shrink-0 h-full flex items-center">
                        <span class="row-sec font-bold truncate block
                            {isActive ? 'text-white/90' : 'text-white/50'}">
                            {row.sector}
                        </span>
                    </div>

                    <!-- average return -->
                    <div style="width:{avgW}" class="pr-2 text-right flex-shrink-0">
                        <span class="row-avg font-black font-mono tabular-nums"
                              style="{avgTextColor(row.avg_return_pct)}">
                            {fmt(row.avg_return_pct)}
                        </span>
                    </div>

                    <!-- per-index cells -->
                    {#each indices as idx}
                        {@const val = row.indices?.[idx]?.return_pct ?? null}
                        <div style="width:{idxW}" class="px-1 flex-shrink-0" title="{INDEX_FULL[idx]||idx}: {fmt(val)}">
                            <div class="cell-inner rounded flex items-center justify-center transition-colors duration-300"
                                 style="{cellBg(val)}">
                                <span class="cell-text font-mono tabular-nums font-bold"
                                      style="{cellTextColor(val)}">
                                    {useShort ? fmtShort(val) : fmt(val)}
                                </span>
                            </div>
                        </div>
                    {/each}
                </button>
            </div>
        {/each}
    </div>
</div>

<style>
    /* container queries drive responsive sizing via cqh units */
    .heatmap-root { container-type: size; }

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
</style>