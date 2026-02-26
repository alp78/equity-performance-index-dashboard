<!--
  ═══════════════════════════════════════════════════════════════════════════
   CorrelationHeatmap — Cross-Index Pearson Correlation Matrix
  ═══════════════════════════════════════════════════════════════════════════
   Heatmap showing how closely each pair of market indices moves together.
   Colour scale runs from red (negative correlation) through white (zero)
   to green (positive).  Diagonal cells are disabled (self-correlation = 1).
   Clicking a cell emits onCellClick for parent to highlight the pair.

   Data source : GET /correlation?period={p}
   Placement   : sidebar "Global Macro" mode, right column
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { INDEX_CONFIG } from '$lib/stores.js';
    import { INDEX_COLORS } from '$lib/theme.js';

    let {
        currentPeriod = '1y',
        customRange = null,
        onCellClick = null,
        highlightPair = null,
        highlightIndex = null,
    } = $props();

    function isHighlighted(rowLabel, colLabel) {
        if (!highlightPair) return false;
        return (rowLabel === highlightPair.row && colLabel === highlightPair.col)
            || (rowLabel === highlightPair.col && colLabel === highlightPair.row);
    }

    function isInHighlightedRowOrCol(label) {
        if (!highlightPair) return false;
        return label === highlightPair.row || label === highlightPair.col;
    }

    function isDimmed(rowLabel, colLabel, isDiag) {
        // Index highlight from sidebar click takes priority
        if (highlightIndex) {
            return rowLabel !== highlightIndex && colLabel !== highlightIndex;
        }
        if (!highlightPair) return false;
        if (isDiag) return !isInHighlightedRowOrCol(rowLabel);
        return !isHighlighted(rowLabel, colLabel);
    }

    function isIndexHighlightedLabel(label) {
        return highlightIndex && label === highlightIndex;
    }

    let matrix = $state([]);
    let labels = $state([]);
    let loading = $state(false);
    let cache = {};

    let pearsonTip = $state(false);
    let tipTimer;
    function showPearsonTip() { if (tipTimer) clearTimeout(tipTimer); pearsonTip = true; }
    function hidePearsonTip() { tipTimer = setTimeout(() => { pearsonTip = false; }, 150); }
    function keepPearsonTip() { if (tipTimer) clearTimeout(tipTimer); }

    // Use INDEX_CONFIG[key].flag / .abbr from stores.js

    let periodKey = $derived(customRange ? `custom_${customRange.start}_${customRange.end}` : (currentPeriod || '1y'));

    $effect(() => {
        const p = customRange ? 'max' : (currentPeriod || '1y');
        fetchCorrelation(p, periodKey);
    });

    async function fetchCorrelation(period, cacheKey) {
        if (cache[cacheKey]) {
            matrix = cache[cacheKey].matrix;
            labels = cache[cacheKey].labels;
            return;
        }
        loading = true;
        try {
            const res = await fetch(`${API_BASE_URL}/correlation?period=${period}`);
            if (res.ok) {
                const data = await res.json();
                matrix = data.matrix || [];
                labels = data.labels || [];
                cache[cacheKey] = data;
            }
        } catch (e) {
            console.error('Correlation fetch error:', e);
        }
        loading = false;
    }

    function cellBg(val, isDiag) {
        if (isDiag) return 'background:var(--hover-overlay)';
        if (val == null) return 'background:var(--surface-1)';
        const pct = Math.round(Math.abs(val) * 18);
        return `background:color-mix(in srgb, var(--text-primary) ${pct}%, var(--surface-1))`;
    }

    function cellTextColor(val, isDiag) {
        if (isDiag) return 'color:var(--text-disabled)';
        if (val == null) return 'color:var(--text-disabled)';
        const a = (0.55 + Math.abs(val) * 0.45).toFixed(2);
        return `color:var(--text-primary); opacity:${a}`;
    }

    function fmtDate(d) {
        if (!d) return '';
        const dt = new Date(d + 'T00:00:00');
        return `${dt.getDate()} ${dt.toLocaleDateString('en-GB', { month: 'short' })} '${String(dt.getFullYear()).slice(2)}`;
    }

    let periodLabel = $derived(
        customRange && customRange.start
            ? `${fmtDate(customRange.start)} → ${fmtDate(customRange.end)}`
            : (currentPeriod || '1y').toUpperCase()
    );
</script>

<Card fill padding={false} class="heatmap-root bg-bg-hover min-h-0">

    <!-- header -->
    <div class="px-5 pt-5 pb-3 shrink-0 border-b border-border">
        <SectionHeader title="Correlation Matrix">
            {#snippet action()}
                <div class="flex items-center gap-2">
                    <span class="text-[11px] font-semibold text-accent uppercase tracking-wider">{periodLabel}</span>
                    {#if loading}
                        <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin" aria-hidden="true"></div>
                    {/if}
                </div>
            {/snippet}
        </SectionHeader>
        <div class="flex items-center gap-3 mt-1">
            <div class="relative inline-block" role="button"
                 onmouseenter={showPearsonTip} onmouseleave={hidePearsonTip}
                 onfocus={showPearsonTip} onblur={hidePearsonTip}
                 tabindex="0">
                <span class="text-[13px] font-medium text-text-faint uppercase tracking-wider cursor-default
                    border-b border-dashed border-border hover:text-text-muted hover:border-border-subtle transition-colors">Pearson daily returns</span>
                {#if pearsonTip}
                    <div class="tt-card" role="tooltip" onmouseenter={keepPearsonTip} onmouseleave={hidePearsonTip}>
                        <div class="text-[14px] font-semibold text-text uppercase tracking-wider mb-1">Pearson Correlation</div>
                        <div class="text-[13px] text-text-muted leading-relaxed mb-2.5">Measures how closely two indices move together day-to-day. Computed from daily return pairs over the selected period.</div>
                        <div class="flex items-start gap-2 mb-1.5">
                            <span class="text-[12px] font-bold uppercase w-20 shrink-0" style="color: var(--text-primary); opacity: 0.95">+0.7 → +1.0</span>
                            <span class="text-[13px] text-text-muted leading-snug">Strong positive — indices move in sync</span>
                        </div>
                        <div class="flex items-start gap-2 mb-1.5">
                            <span class="text-[12px] font-bold uppercase w-20 shrink-0" style="color: var(--text-primary); opacity: 0.65">+0.3 → +0.7</span>
                            <span class="text-[13px] text-text-muted leading-snug">Moderate — some shared direction</span>
                        </div>
                        <div class="flex items-start gap-2 mb-1.5">
                            <span class="text-[12px] font-bold uppercase w-20 shrink-0" style="color: var(--text-primary); opacity: 0.35">  0 → +0.3</span>
                            <span class="text-[13px] text-text-muted leading-snug">Weak positive — slight shared tendency</span>
                        </div>
                        <div class="flex items-start gap-2 mb-1.5">
                            <span class="text-[12px] font-bold uppercase w-20 shrink-0" style="color: var(--text-primary); opacity: 0.25">−0.3 → 0</span>
                            <span class="text-[13px] text-text-muted leading-snug">Weak negative — slight divergence</span>
                        </div>
                        <div class="flex items-start gap-2">
                            <span class="text-[12px] font-bold uppercase w-20 shrink-0" style="color: var(--text-primary); opacity: 0.15">−1.0 → −0.3</span>
                            <span class="text-[13px] text-text-muted leading-snug">Negative — indices move in opposite directions</span>
                        </div>
                    </div>
                {/if}
            </div>
            {#if highlightPair}
                <div class="ml-auto flex items-center gap-1.5">
                    <span class="text-[13px] font-bold text-text-secondary uppercase tracking-wider">
                        {INDEX_CONFIG[highlightPair.row]?.abbr || highlightPair.row} × {INDEX_CONFIG[highlightPair.col]?.abbr || highlightPair.col}
                    </span>
                    <button class="text-[11px] text-text-faint hover:text-text-secondary transition-colors" aria-label="Clear correlation selection" onclick={() => onCellClick?.(highlightPair)}>
                        ✕ clear
                    </button>
                </div>
            {/if}
        </div>
    </div>

    {#if matrix.length === 0 && !loading}
        <div class="flex-1 flex items-center justify-center">
            <span class="text-[14px] text-text-muted">No correlation data</span>
        </div>
    {:else if matrix.length > 0}
        <div class="flex-1 min-h-0 p-3 flex flex-col">
            <!-- column headers -->
            <div class="flex shrink-0 col-hdr-row" style="padding-left: 15%;">
                {#each labels as label}
                    {@const colActive = isIndexHighlightedLabel(label) || (highlightPair && isInHighlightedRowOrCol(label))}
                    {@const anyActive = highlightIndex || highlightPair}
                    <div class="flex-1 flex flex-col items-center gap-0.5 transition-opacity"
                         style="opacity: {anyActive && !colActive ? 0.25 : 1};">
                        <span class="col-hdr-text font-semibold uppercase tracking-wider"
                              style="color: {INDEX_COLORS[label] || '#fff'}; opacity: {colActive ? 1 : 0.8}; {colActive ? 'text-decoration: underline; text-underline-offset: 4px; text-decoration-thickness: 2px;' : ''}">
                            {INDEX_CONFIG[label]?.abbr || label}
                        </span>
                    </div>
                {/each}
            </div>

            <!-- matrix rows -->
            <div class="flex-1 flex flex-col min-h-0" role="grid" aria-label="Index correlation matrix">
                {#each labels as rowLabel, i}
                    {@const rowActive = isIndexHighlightedLabel(rowLabel) || (highlightPair && isInHighlightedRowOrCol(rowLabel))}
                    {@const anyRowActive = highlightIndex || highlightPair}
                    <div class="flex-1 flex items-center min-h-0 row-wrap" role="row">
                        <!-- row header -->
                        <div class="w-[15%] flex items-center gap-1.5 pr-2 shrink-0 transition-opacity"
                             style="opacity: {anyRowActive && !rowActive ? 0.25 : 1};">
                            <span class="row-label font-semibold uppercase tracking-wider truncate"
                                  style="color: {INDEX_COLORS[rowLabel] || '#fff'}; opacity: {rowActive ? 1 : 0.8}; {rowActive ? 'text-decoration: underline; text-underline-offset: 4px; text-decoration-thickness: 2px;' : ''}">
                                {INDEX_CONFIG[rowLabel]?.abbr || rowLabel}
                            </span>
                        </div>
                        <!-- cells -->
                        {#each labels as colLabel, j}
                            {@const val = matrix[i][j]}
                            {@const isDiag = i === j}
                            {@const highlighted = isHighlighted(rowLabel, colLabel)}
                            {@const dimmed = isDimmed(rowLabel, colLabel, isDiag)}
                            <button
                                role="gridcell"
                                class="flex-1 flex items-center justify-center rounded-sm cell-inner transition-all
                                    {isDiag ? 'cursor-default' : 'cursor-pointer hover:ring-1 hover:ring-border hover:scale-[1.03]'}
                                    {highlighted ? 'ring-2 ring-orange-400/70 scale-[1.05] z-10' : ''}"
                                style="{cellBg(val, isDiag)}; {cellTextColor(val, isDiag)}; {dimmed ? 'opacity: 0.2;' : ''}"
                                onclick={() => !isDiag && onCellClick?.({ row: rowLabel, col: colLabel, value: val })}
                                disabled={isDiag}
                            >
                                <span class="cell-text font-medium tabular-nums">
                                    {isDiag ? '—' : val != null ? val.toFixed(2) : '—'}
                                </span>
                            </button>
                        {/each}
                    </div>
                {/each}
            </div>

            <!-- legend -->
            <div class="flex items-center justify-center gap-3 mt-2 shrink-0">
                <span class="legend-text font-bold text-disabled">-1.0</span>
                <div class="h-2 w-24 rounded-full" style="background: linear-gradient(to right, var(--surface-1), var(--border-strong));"></div>
                <span class="legend-text font-bold text-primary">+1.0</span>
            </div>
        </div>
    {/if}
</Card>

<style>
    .tt-card {
        position: absolute;
        top: calc(100% + 8px);
        left: 0;
        z-index: 50;
        width: min(340px, calc(100vw - 2rem));
        padding: var(--space-md) var(--space-lg);
        background: var(--surface-overlay);
        border: 1px solid var(--border-default);
        border-radius: var(--radius-lg);
        backdrop-filter: blur(8px);
        box-shadow: var(--shadow-tooltip);
    }

    :global(.heatmap-root) { container-type: size; }

    .col-hdr-row  { padding-top: clamp(3px, 0.8cqh, 8px); padding-bottom: clamp(3px, 0.8cqh, 8px); }
    .col-hdr-text { font-size: clamp(13px, 2.2cqh, 18px); }
    .row-label    { font-size: clamp(13px, 2.2cqh, 18px); }
    .row-wrap     { min-height: 0; }
    .cell-inner   { min-height: 0; height: 100%; margin-top: 1px; margin-bottom: 1px; }
    .cell-text    { font-size: clamp(13px, 2cqh, 18px); }
    .legend-text  { font-size: clamp(10px, 1.3cqh, 12px); }
</style>
