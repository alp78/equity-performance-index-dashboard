<script>
    import { browser } from '$app/environment';
    import { onMount, onDestroy } from 'svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { singleSelectedIndex, selectedSector, selectedIndustries, singleModeIndustries } from '$lib/stores.js';
    import { INDEX_CONFIG } from '$lib/index-registry.js';
    import { getSectorColor, getSectorShades } from '$lib/theme.js';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { fmtDate } from '$lib/format.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let rows     = $state([]);
    let loading  = $state(false);
    let hasEverLoaded = $state(false);
    let cache    = {};
    let chartEl;
    let chart = $state(null);
    let observer;
    let isMobile = $state(typeof window !== 'undefined' && window.innerWidth < 640);

    let abortCtrl = null;
    let loadingTimer = null;

    let indexKey  = $derived(($singleSelectedIndex || [])[0] || 'sp500');
    let sector    = $derived($selectedSector || '');
    let indexCfg  = $derived(INDEX_CONFIG?.[indexKey] || {});
    let currSym   = $derived(indexCfg.currency || '$');

    // Industries filtered for the CURRENT sector only (not global combined list)
    let currentSectorIndustries = $derived(
        sector ? ($singleModeIndustries?.[sector] || []) : []
    );

    // --- PERIOD LABEL ---

    let isCustom    = $derived(!!(customRange?.start));
    let periodLabel = $derived(
        isCustom ? `${fmtDate(customRange.start)} → ${fmtDate(customRange.end)}`
            : (currentPeriod || '1y').toUpperCase()
    );

    // Base color for palette generation
    let sectorBaseColor = $derived(sector ? getSectorColor(sector) : '#94A3B8');

    // --- FORMAT ---

    function fmtTurnover(val, sym) {
        const s = sym || currSym;
        if (val == null || val === 0) return '—';
        const abs = Math.abs(val);
        if (abs >= 1e12) return s + (val / 1e12).toFixed(1) + 'T';
        if (abs >= 1e9)  return s + (val / 1e9).toFixed(1) + 'B';
        if (abs >= 1e6)  return s + (val / 1e6).toFixed(1) + 'M';
        if (abs >= 1e3)  return s + (val / 1e3).toFixed(0) + 'K';
        return s + val.toFixed(0);
    }

    // --- DATA LOADING ---

    async function load(period, range, idx, sec) {
        if (!browser || !idx || !sec) return;
        
        const pKey = range?.start ? `${range.start}_${range.end}` : (period || '1y');
        const cKey = `turnover_${idx}_${sec}_${pKey}`;
        
        if (cache[cKey]) { 
            rows = cache[cKey]; 
            hasEverLoaded = true; 
            return; 
        }

        if (abortCtrl) abortCtrl.abort();
        abortCtrl = new AbortController();

        clearTimeout(loadingTimer);
        loadingTimer = setTimeout(() => { loading = true; }, 150);

        try {
            const base = `${API_BASE_URL}/sector-comparison/industry-turnover?index=${idx}&sector=${encodeURIComponent(sec)}`;
            const url  = range?.start
                ? `${base}&start=${range.start}&end=${range.end}`
                : `${base}&period=${period||'1y'}`;
            
            const t = setTimeout(() => abortCtrl.abort(), 12000);
            const res = await fetch(url, { signal: abortCtrl.signal });
            clearTimeout(t);
            
            if (res.ok) {
                const data = await res.json();
                cache[cKey] = data;
                rows = data;
            }
        } catch (err) {
            // Fetch aborts will be caught here; safely ignored
        } finally {
            clearTimeout(loadingTimer);
            if (!abortCtrl.signal.aborted) {
                loading = false;
                hasEverLoaded = true;
            }
        }
    }

    $effect(() => { load(currentPeriod, customRange, indexKey, sector); });

    // --- FILTERING ---

    let filteredRows = $derived(
        $selectedIndustries.length === 0
            ? rows
            : rows.filter(r => $selectedIndustries.includes(r.industry))
    );

    let totalTurnover = $derived(
        filteredRows.reduce((sum, r) => sum + (r.turnover || 0), 0)
    );

    // group small slices (<2%) into "Other"
    let pieData = $derived((() => {
        if (!filteredRows.length || totalTurnover <= 0) return [];
        const threshold = totalTurnover * 0.02;
        const main = [];
        let otherTurnover = 0;
        let otherCount = 0;
        for (const r of filteredRows) {
            if (r.turnover >= threshold) {
                main.push({ name: r.industry, value: r.turnover, stockCount: r.stock_count });
            } else {
                otherTurnover += r.turnover;
                otherCount += r.stock_count;
            }
        }
        if (otherTurnover > 0) {
            main.push({ name: 'Other', value: otherTurnover, stockCount: otherCount });
        }
        return main;
    })());

    // Dynamic palette — shade count matches actual pie slices for maximum contrast
    let sliceCount = $derived(Math.max(pieData.length, 2));
    let PALETTE = $derived(getSectorShades(sectorBaseColor, sliceCount));

    // --- ECHARTS ---

    function buildOption(data) {
        return {
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'item',
                appendToBody: true,
                backgroundColor: 'var(--surface-overlay, rgba(30,35,48,0.95))',
                borderColor: 'var(--border-default, rgba(36,48,64,0.5))',
                borderWidth: 1,
                padding: [14, 18],
                textStyle: {
                    color: 'var(--text-primary, rgba(232,236,241,0.85))',
                    fontSize: 16,
                    fontFamily: 'Geist, Inter, system-ui, sans-serif',
                },
                formatter: (params) => {
                    const d = params.data;
                    return `<div style="font-size:18px;font-weight:700;margin-bottom:6px">${params.name}</div>`
                        + `<div style="color:var(--text-muted);font-size:15px;line-height:1.7">`
                        + `Turnover: <span style="color:var(--text-primary);font-weight:600;font-variant-numeric:tabular-nums">${fmtTurnover(d.value)}</span><br/>`
                        + `Share: <span style="color:var(--text-primary);font-weight:600;font-variant-numeric:tabular-nums">${params.percent}%</span><br/>`
                        + `Stocks: <span style="color:var(--text-primary);font-weight:600;font-variant-numeric:tabular-nums">${d.stockCount}</span>`
                        + `</div>`;
                },
            },
            series: [{
                type: 'pie',
                center: ['50%', '52%'],
                radius: ['35%', '72%'],
                data: data,
                label: {
                    show: true,
                    position: 'outside',
                    formatter: '{b}',
                    color: '#C8CDD5',
                    fontSize: isMobile ? 11 : 14,
                    fontWeight: 500,
                    fontFamily: 'Geist, Inter, system-ui, sans-serif',
                    overflow: 'truncate',
                    ellipsis: '..',
                    width: isMobile ? 70 : 120,
                },
                labelLine: {
                    show: true,
                    length: isMobile ? 8 : 14,
                    length2: isMobile ? 10 : 18,
                    smooth: 0.3,
                    lineStyle: {
                        color: 'var(--border-subtle, rgba(26,32,48,1))',
                        width: 1,
                    },
                },
                labelLayout: {
                    hideOverlap: true,
                },
                emphasis: {
                    scale: true,
                    scaleSize: 6,
                    label: {
                        show: true,
                        color: 'var(--text-primary, #E8ECF1)',
                        fontSize: 16,
                        fontWeight: 'bold',
                    },
                    itemStyle: {
                        shadowBlur: 16,
                        shadowColor: 'rgba(0,0,0,0.5)',
                    },
                },
                itemStyle: {
                    borderColor: 'var(--surface-0, #0A0D12)',
                    borderWidth: 1,
                },
                animationType: 'scale',
                animationEasing: 'cubicOut',
                animationDuration: 500,
                animationDurationUpdate: 300,
                animationEasingUpdate: 'cubicInOut',
                color: PALETTE,
            }],
        };
    }

    // update chart whenever pieData changes — skip while loading to prevent blink
    $effect(() => {
        if (!chart || loading) return;
        if (pieData.length === 0) {
            chart.clear();
        } else {
            chart.setOption(buildOption(pieData));
        }
    });

    onMount(async () => {
        if (!browser || !chartEl) return;
        const echarts = await import('echarts');
        const instance = echarts.init(chartEl, 'dark', { renderer: 'canvas' });
        observer = new ResizeObserver(() => {
            instance?.resize();
            const wasMobile = isMobile;
            isMobile = window.innerWidth < 640;
            // re-render with updated label sizes if breakpoint crossed
            if (wasMobile !== isMobile && pieData.length > 0) {
                instance?.setOption(buildOption(pieData), { notMerge: true });
            }
        });
        observer.observe(chartEl);
        chart = instance; // triggers $effect to render
    });

    onDestroy(() => {
        observer?.disconnect();
        chart?.dispose();
    });
</script>

<Card fill padding={false} class="min-h-0">

    <div class="px-5 pt-5 pb-3 flex-shrink-0">
        <SectionHeader title="Industry Turnover" subtitle="Turnover ({currSym})" border titleClass={currentSectorIndustries.length > 0 ? 'text-blue-400' : ''}>
            {#snippet action()}
                <span class="text-[11px] font-semibold text-accent uppercase tracking-wider">{periodLabel}</span>
                {#if loading}
                    <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin flex-shrink-0"></div>
                {/if}
            {/snippet}
        </SectionHeader>
        {#if sector}
            <div class="flex items-center gap-1.5 mt-1 flex-wrap">
                <span class="text-[13px] font-semibold uppercase tracking-wider" style="color:{getSectorColor(sector)}">{sector}</span>
            </div>
        {/if}
    </div>

    <div class="flex-1 min-h-0 relative">
        {#if pieData.length === 0 && !loading && !hasEverLoaded && sector}
            <div class="absolute inset-0 flex items-center justify-center">
                <div class="w-4 h-4 border border-border border-t-text-muted rounded-full animate-spin"></div>
            </div>
        {:else if pieData.length === 0 && !loading}
            <div class="absolute inset-0 flex items-center justify-center">
                <span class="text-text-faint text-[12px] font-medium uppercase tracking-widest">
                    {sector ? 'No data' : 'Select a sector'}
                </span>
            </div>
        {/if}
        <div bind:this={chartEl} class="w-full h-full"></div>
    </div>
</Card>