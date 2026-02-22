<!-- industry turnover pie chart using ECharts, with period header -->

<script>
    import { browser } from '$app/environment';
    import { onMount, onDestroy } from 'svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { singleSelectedIndex, selectedSector, selectedIndustries, INDEX_CONFIG } from '$lib/stores.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let rows     = $state([]);
    let loading  = $state(false);
    let cache    = {};
    let chartEl;
    let chart = $state(null);
    let observer;

    let indexKey  = $derived(($singleSelectedIndex || [])[0] || 'sp500');
    let sector    = $derived($selectedSector || '');
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

    // Tableau 10 darkened ~40% for dark theme
    const PALETTE = [
        '#2f4964', '#91551a', '#873435', '#476e6b', '#365f2f',
        '#8e792c', '#694961', '#995e64', '#5d4639', '#6f6a66',
        '#13476c', '#801818', '#583d71', '#0e727c', '#884774',
    ];

    // --- FORMAT ---

    function fmtTurnover(val) {
        if (val == null || val === 0) return '—';
        const abs = Math.abs(val);
        if (abs >= 1e12) return '$' + (val / 1e12).toFixed(1) + 'T';
        if (abs >= 1e9)  return '$' + (val / 1e9).toFixed(1) + 'B';
        if (abs >= 1e6)  return '$' + (val / 1e6).toFixed(1) + 'M';
        if (abs >= 1e3)  return '$' + (val / 1e3).toFixed(0) + 'K';
        return '$' + val.toFixed(0);
    }

    // --- DATA LOADING ---

    async function load(period, range, idx, sec) {
        if (!browser || !idx || !sec) return;
        const pKey = range?.start ? `${range.start}_${range.end}` : (period || '1y');
        const cKey = `turnover_${idx}_${sec}_${pKey}`;
        if (cache[cKey]) { rows = cache[cKey]; return; }
        loading = true;
        try {
            const base = `${API_BASE_URL}/sector-comparison/industry-turnover?index=${idx}&sector=${encodeURIComponent(sec)}`;
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

    // --- ECHARTS ---

    function buildOption(data) {
        return {
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'item',
                backgroundColor: 'rgba(12, 10, 18, 0.95)',
                borderColor: 'rgba(255,255,255,0.06)',
                borderWidth: 1,
                padding: [14, 18],
                textStyle: {
                    color: 'rgba(255,255,255,0.85)',
                    fontSize: 14,
                    fontFamily: 'Inter, system-ui, sans-serif',
                },
                formatter: (params) => {
                    const d = params.data;
                    return `<div style="font-size:16px;font-weight:700;margin-bottom:6px">${params.name}</div>`
                        + `<div style="color:rgba(255,255,255,0.5);font-size:13px;line-height:1.7">`
                        + `Turnover: <span style="color:rgba(255,255,255,0.9);font-weight:600">${fmtTurnover(d.value)}</span><br/>`
                        + `Share: <span style="color:${params.color};font-weight:600">${params.percent}%</span><br/>`
                        + `Stocks: <span style="color:rgba(255,255,255,0.9);font-weight:600">${d.stockCount}</span>`
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
                    formatter: '{b}  {d}%',
                    color: 'rgba(255,255,255,0.55)',
                    fontSize: 13,
                    fontWeight: 500,
                    fontFamily: 'Inter, system-ui, sans-serif',
                    overflow: 'truncate',
                    ellipsis: '..',
                    width: 120,
                },
                labelLine: {
                    show: true,
                    length: 14,
                    length2: 18,
                    smooth: 0.3,
                    lineStyle: {
                        color: 'rgba(255,255,255,0.15)',
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
                        color: 'rgba(255,255,255,0.9)',
                        fontSize: 14,
                        fontWeight: 'bold',
                    },
                    itemStyle: {
                        shadowBlur: 16,
                        shadowColor: 'rgba(0,0,0,0.5)',
                    },
                },
                itemStyle: {
                    borderColor: '#0c0a12',
                    borderWidth: 1,
                },
                animationType: 'scale',
                animationEasing: 'cubicOut',
                animationDuration: 500,
                color: PALETTE,
            }],
        };
    }

    // update chart whenever pieData changes
    $effect(() => {
        if (!chart) return;
        if (pieData.length === 0) {
            chart.clear();
        } else {
            chart.setOption(buildOption(pieData), { notMerge: true });
        }
    });

    onMount(async () => {
        if (!browser || !chartEl) return;
        const echarts = await import('echarts');
        const instance = echarts.init(chartEl, 'dark', { renderer: 'canvas' });
        observer = new ResizeObserver(() => { instance?.resize(); });
        observer.observe(chartEl);
        chart = instance; // triggers $effect to render
    });

    onDestroy(() => {
        observer?.disconnect();
        chart?.dispose();
    });
</script>

<div class="h-full w-full flex flex-col bg-white/[0.03] rounded-2xl border border-white/5 overflow-hidden min-h-0">

    <!-- header -->
    <div class="flex items-start justify-between px-5 pt-5 pb-3 flex-shrink-0 border-b border-white/5">
        <div class="flex flex-col items-start">
            <div class="flex items-center gap-2">
                <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">Industry Turnover</h3>
                <span class="text-[9px] font-black text-orange-400 uppercase tracking-wider">{periodLabel}</span>
            </div>
            <span class="text-[11px] font-black uppercase tracking-wider mt-1"
                  style="color:{indexCfg.color || '#8b5cf6'}">
                {sector || '—'}
            </span>
        </div>
        {#if loading}
            <div class="w-3 h-3 border border-white/10 border-t-white/40 rounded-full animate-spin mt-1 flex-shrink-0"></div>
        {/if}
    </div>

    <!-- chart area -->
    <div class="flex-1 min-h-0 relative">
        {#if pieData.length === 0 && !loading}
            <div class="absolute inset-0 flex items-center justify-center">
                <span class="text-white/15 text-[10px] font-bold uppercase tracking-widest">
                    {sector ? 'No data' : 'Select a sector'}
                </span>
            </div>
        {/if}
        <div bind:this={chartEl} class="w-full h-full"></div>
    </div>
</div>
