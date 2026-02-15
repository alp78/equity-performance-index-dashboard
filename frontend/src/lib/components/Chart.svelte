<script>
    import { onMount, onDestroy } from 'svelte';
    import { createChart, ColorType, AreaSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle } from 'lightweight-charts';

    let { data = [] } = $props();
    let chartContainer, tooltip, chart, priceSeries, volumeSeries, ma30Series, ma90Series;
    let observer;

    function formatDate(timeStr) {
        if (!timeStr) return "";
        const [year, month, day] = timeStr.split('-');
        return `${day}/${month}/${year}`;
    }

    onMount(() => {
        chart = createChart(chartContainer, {
            layout: { 
                background: { type: ColorType.Solid, color: '#0d0d12' }, 
                textColor: '#94a3b8',
                fontFamily: 'Inter, sans-serif'
            },
            grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(255, 255, 255, 0.03)' } },
            rightPriceScale: { 
                borderVisible: false, 
                scaleMargins: { top: 0.1, bottom: 0.3 },
                visible: true 
            },
            timeScale: { 
                borderVisible: false, 
                fixLeftEdge: true,   
                fixRightEdge: true,  
                rightOffset: 0,
                barSpacing: 10,
            },
            crosshair: { 
                mode: CrosshairMode.Magnet, 
                vertLine: { color: 'rgba(168, 85, 247, 0.4)', labelVisible: false }, 
                horzLine: { color: 'rgba(168, 85, 247, 0.4)', labelVisible: true } 
            },
            handleScroll: false,
            handleScale: false
        });

        // Branding Removal
        const nuke = () => {
            const targets = chartContainer.querySelectorAll('div, a, span');
            targets.forEach(el => {
                if (el.innerText?.includes('TradingView') || el.href?.includes('tradingview')) {
                    el.style.display = 'none';
                }
            });
        };
        nuke();
        observer = new MutationObserver(nuke);
        observer.observe(chartContainer, { childList: true, subtree: true });

        // Series setup
        priceSeries = chart.addSeries(AreaSeries, {
            lineColor: '#a855f7', 
            topColor: 'rgba(168, 85, 247, 0.3)', 
            bottomColor: 'rgba(168, 85, 247, 0)', 
            lineWidth: 3,
            lastValueVisible: true,
            priceLineVisible: true,
            priceLineColor: '#ffffff',
            priceLineStyle: LineStyle.Dashed
        });

        ma30Series = chart.addSeries(LineSeries, { color: '#22d3ee', lineWidth: 1, lineStyle: LineStyle.Dashed, lastValueVisible: false, priceLineVisible: false });
        ma90Series = chart.addSeries(LineSeries, { color: '#6366f1', lineWidth: 1, lineStyle: LineStyle.Dashed, lastValueVisible: false, priceLineVisible: false });
        volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: true });

        chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, borderVisible: false });

        // BOUNDARY AWARE TOOLTIP
        chart.subscribeCrosshairMove((param) => {
            if (!tooltip || !param.time || !param.point || param.point.x < 0) { 
                if (tooltip) tooltip.style.display = 'none'; 
                return; 
            }
            const pData = param.seriesData.get(priceSeries);
            const vData = param.seriesData.get(volumeSeries);
            const m30Data = param.seriesData.get(ma30Series);
            const m90Data = param.seriesData.get(ma90Series);

            if (pData && data.length > 0) {
                const livePrice = data[data.length - 1].close;
                const diff = livePrice - pData.value;
                const diffPct = (diff / pData.value) * 100;
                const colorClass = diff >= 0 ? 'text-green-500' : 'text-red-500';

                tooltip.style.display = 'flex';
                
                // --- BOUNDARY DETECTION LOGIC ---
                const tooltipWidth = 180;
                const tooltipHeight = 180;
                const containerWidth = chartContainer.clientWidth;
                const containerHeight = chartContainer.clientHeight;

                let left = param.point.x + 20;
                let top = param.point.y + 20;

                // If tooltip would go off the right edge, flip to left of cursor
                if (left + tooltipWidth > containerWidth) {
                    left = param.point.x - tooltipWidth - 20;
                }
                
                // If tooltip would go off the bottom edge, push it up
                if (top + tooltipHeight > containerHeight) {
                    top = param.point.y - tooltipHeight - 20;
                }
                // --------------------------------

                tooltip.style.left = `${left}px`;
                tooltip.style.top = `${top}px`;
                tooltip.innerHTML = `
                    <div class="flex flex-col p-3 bg-[#16161e]/95 border border-white/10 rounded-xl shadow-2xl backdrop-blur-md min-w-[160px] gap-1.5 pointer-events-none">
                        <span class="text-[9px] text-white/30 uppercase font-black tracking-widest border-b border-white/5 pb-1 mb-1">${formatDate(param.time)}</span>
                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[9px] text-white/60 uppercase font-bold">Hist. Price</span>
                            <span class="text-xs text-white font-black">$${pData.value.toFixed(2)}</span>
                        </div>
                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[9px] text-white/40 uppercase font-bold">vs Live</span>
                            <span class="text-[10px] font-black ${colorClass}">
                                ${diff >= 0 ? '+' : ''}${diff.toFixed(2)} (${diffPct.toFixed(2)}%)
                            </span>
                        </div>
                        <div class="flex justify-between items-center gap-4 border-t border-white/5 pt-1 mt-1">
                            <span class="text-[9px] text-cyan-400/60 uppercase font-bold">MA 30</span>
                            <span class="text-xs text-cyan-400 font-bold">${m30Data ? '$' + m30Data.value.toFixed(2) : '—'}</span>
                        </div>
                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[9px] text-indigo-400/60 uppercase font-bold">MA 90</span>
                            <span class="text-xs text-indigo-400 font-bold">${m90Data ? '$' + m90Data.value.toFixed(2) : '—'}</span>
                        </div>
                        <div class="flex justify-between items-center gap-4 pt-1 border-t border-white/5">
                            <span class="text-[9px] text-purple-400/60 uppercase font-bold">Vol</span>
                            <span class="text-xs text-purple-400 font-bold">${vData ? (vData.value / 1000000).toFixed(1) + 'M' : '—'}</span>
                        </div>
                    </div>`;
            }
        });

        const resizer = new ResizeObserver(() => {
            chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
            chart.timeScale().fitContent();
        });
        resizer.observe(chartContainer);
    });

    $effect(() => {
        const raw = $state.snapshot(data);
        if (priceSeries && raw.length > 0) {
            // FIX START: DEDUPLICATE AND SORT DATA BY TIME
            const uniqueMap = new Map();
            raw.forEach(item => uniqueMap.set(item.time, item));
            const uniqueSorted = Array.from(uniqueMap.values())
                                      .sort((a, b) => new Date(a.time) - new Date(b.time));
            // FIX END

            priceSeries.setData(uniqueSorted.map(d => ({ time: d.time, value: d.close })));
            ma30Series.setData(uniqueSorted.map(d => ({ time: d.time, value: d.ma30 })));
            ma90Series.setData(uniqueSorted.map(d => ({ time: d.time, value: d.ma90 })));
            
            const maxVol = Math.max(...uniqueSorted.map(d => d.volume));
            volumeSeries.setData(uniqueSorted.map(d => ({
                time: d.time, value: d.volume,
                color: d.volume > (maxVol * 0.8) ? '#a855f7' : 'rgba(168, 85, 247, 0.3)'
            })));

            const timeScale = chart.timeScale();
            timeScale.fitContent();
            requestAnimationFrame(() => {
                const logicalRange = timeScale.getVisibleLogicalRange();
                if (logicalRange) {
                    timeScale.setVisibleLogicalRange({
                        from: logicalRange.from + 0.5,
                        to: logicalRange.to - 0.5
                    });
                }
            });
        }
    });

    onDestroy(() => {
        observer?.disconnect();
        chart?.remove();
    });
</script>

<div bind:this={chartContainer} class="w-full h-full min-h-[500px] relative rounded-2xl overflow-hidden bg-[#0d0d12]">
    <div class="absolute top-4 left-6 z-[110] flex gap-5 pointer-events-none p-2 bg-black/20 backdrop-blur-sm rounded-lg">
        <div class="flex items-center gap-2"><div class="w-4 h-[2px] bg-[#a855f7]"></div><span class="text-[9px] text-white/60 uppercase font-black tracking-widest">Price</span></div>
        <div class="flex items-center gap-2"><div class="w-4 h-0 border-t-2 border-dashed border-white"></div><span class="text-[9px] text-white font-black tracking-widest">Live</span></div>
        <div class="flex items-center gap-2"><div class="w-4 h-0 border-t border-dashed border-cyan-400"></div><span class="text-[9px] text-cyan-400/80 uppercase font-black tracking-widest">MA 30</span></div>
        <div class="flex items-center gap-2"><div class="w-4 h-0 border-t border-dashed border-indigo-400"></div><span class="text-[9px] text-indigo-400/80 uppercase font-black tracking-widest">MA 90</span></div>
        <div class="flex items-center gap-2"><div class="w-4 h-0 border-t-2 border-dotted border-[#a855f7]/60"></div><span class="text-[9px] text-white/30 uppercase font-black tracking-widest">Volume</span></div>
    </div>
    <div bind:this={tooltip} class="absolute hidden z-[120] pointer-events-none transition-all duration-75"></div>
</div>

<style>
    :global(.tv-lightweight-charts-attribution) { display: none !important; }
</style>