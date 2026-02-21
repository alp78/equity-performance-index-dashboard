<!--
  IndustryBreakdown.svelte
  Industries within the selected sector, ranked by return.
  Uses /sector-comparison/industry-breakdown endpoint.
  Updates reactively when selectedSector changes.
-->
<script>
    import { browser } from '$app/environment';
    import { API_BASE_URL } from '$lib/config.js';
    import { singleSelectedIndex, selectedSector, selectedIndustries } from '$lib/stores.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let selected = $derived(new Set($selectedIndustries || []));
    let hasSelection = $derived(selected.size > 0);

    function toggleIndustry(industry) {
        selectedIndustries.update(list => {
            if (list.includes(industry)) {
                return list.filter(i => i !== industry);
            }
            return [...list, industry];
        });
    }

    function clearIndustrySelection() {
        selectedIndustries.set([]);
    }

    let rows     = $state([]);
    let loading  = $state(false);
    let cache    = {};

    let indexKey = $derived(($singleSelectedIndex || [])[0] || 'sp500');
    let sector   = $derived($selectedSector || '');

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

    async function load(period, range, idx, sec) {
        if (!browser || !idx || !sec) return;
        const pKey = range?.start ? `${range.start}_${range.end}` : (period || '1y');
        const cKey = `industry_${idx}_${sec}_${pKey}`;
        if (cache[cKey]) { rows = cache[cKey]; return; }
        loading = true;
        try {
            const base = `${API_BASE_URL}/sector-comparison/industry-breakdown?index=${idx}&sector=${encodeURIComponent(sec)}`;
            const url  = range?.start
                ? `${base}&start=${range.start}&end=${range.end}`
                : `${base}&period=${period||'1y'}`;
            const ctrl = new AbortController();
            const t = setTimeout(() => ctrl.abort(), 12000);
            const res = await fetch(url, { signal: ctrl.signal });
            clearTimeout(t);
            if (res.ok) {
                const data = await res.json();
                cache[cKey] = data;
                rows = data;
            }
        } catch {}
        loading = false;
    }

    $effect(() => { load(currentPeriod, customRange, indexKey, sector); });

    let maxAbs    = $derived(Math.max(...rows.map(r => Math.abs(r.return_pct || 0)), 1));
    let totalRows = $derived(rows.length);
    function barW(val) { return `${Math.min(Math.abs(val) / maxAbs * 100, 100).toFixed(1)}%`; }
</script>

<div class="breakdown-root h-full w-full flex flex-col bg-white/[0.03] rounded-2xl border border-white/5 overflow-x-hidden min-h-0">

    <div class="flex items-start justify-between px-5 pt-5 pb-3 flex-shrink-0 border-b border-white/5">
        <div class="flex flex-col items-start">
            <div class="flex items-center gap-2">
                <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">Industry Breakdown</h3>
                <span class="text-[9px] font-black text-orange-400 uppercase tracking-wider">{periodLabel}</span>
            </div>
            <span class="text-[11px] font-black text-white/60 uppercase tracking-wider mt-1">
                {sector || '—'}
            </span>
        </div>
        <div class="flex items-center gap-2">
            {#if hasSelection}
                <button
                    class="text-[9px] font-black text-orange-400/80 uppercase tracking-wider hover:text-orange-300 transition-colors"
                    onclick={clearIndustrySelection}
                >
                    Clear ({selected.size})
                </button>
            {/if}
            {#if loading}
                <div class="w-3 h-3 border border-white/10 border-t-white/40 rounded-full animate-spin mt-1 flex-shrink-0"></div>
            {/if}
        </div>
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 py-2 flex flex-col justify-start gap-2"
         style="--row-count:{Math.max(totalRows, 4)}">

        {#if rows.length === 0 && !loading}
            <div class="flex items-center justify-center h-full text-white/15 text-[10px] font-bold uppercase tracking-widest">
                {sector ? 'No data' : 'Select a sector'}
            </div>
        {/if}

        {#each rows as row (row.industry)}
            {@const pos        = (row.return_pct ?? 0) >= 0}
            {@const barColor   = pos ? 'rgba(34,197,94,0.75)' : 'rgba(239,68,68,0.7)'}
            {@const valColor   = pos ? 'rgba(34,197,94,0.9)'  : 'rgba(239,68,68,0.85)'}
            {@const isSelected = selected.has(row.industry)}
            {@const isDimmed   = hasSelection && !isSelected}

            <button
                class="row-item w-full flex items-center gap-2 px-2 rounded-lg cursor-pointer transition-all duration-150
                    {isSelected ? 'bg-orange-500/10 ring-1 ring-orange-500/30' : 'hover:bg-white/[0.02]'}"
                onclick={() => toggleIndustry(row.industry)}
            >
                <div class="w-1.5 h-1.5 rounded-full flex-shrink-0 transition-all duration-150"
                     style="background:{isSelected ? '#f97316' : 'transparent'}; border: 1px solid {isSelected ? '#f97316' : 'rgba(255,255,255,0.1)'}"></div>
                <span class="ind-name font-bold truncate flex-shrink-0 transition-opacity duration-150"
                      style="width:40%; color:{isSelected ? 'rgba(255,255,255,0.9)' : isDimmed ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.5)'}">
                    {row.industry}
                </span>
                <div class="flex-1 flex items-center gap-2 min-w-0">
                    <div class="flex-1 h-[9px] rounded-full bg-white/[0.05] overflow-hidden min-w-0">
                        <div class="h-full rounded-full transition-all duration-500"
                             style="width:{barW(row.return_pct)}; background:{barColor}; opacity:{isDimmed ? 0.3 : 1}"></div>
                    </div>
                    <span class="ind-val font-black font-mono tabular-nums flex-shrink-0 transition-opacity duration-150"
                          style="color:{valColor}; opacity:{isDimmed ? 0.4 : 1}">
                        {fmt(row.return_pct)}
                    </span>
                </div>
                <span class="ind-ct font-bold tabular-nums text-white/20 flex-shrink-0">{row.stock_count}</span>
            </button>
        {/each}
    </div>
</div>

<style>
    .breakdown-root { container-type: size; }
    .row-item { min-height: 22px; max-height: 46px; flex: 1; }
    .ind-name { font-size: 13px; }
    .ind-val  { font-size: 13px; font-weight: 900; min-width: 58px; text-align: right; }
    .ind-ct   { font-size: 12px; min-width: 20px; text-align: right; }
</style>