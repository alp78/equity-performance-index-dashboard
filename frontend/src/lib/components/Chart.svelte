<!--
  Chart Component
  ===============
  Two modes: BROWSE (zoom/pan) and SELECT (brush for custom range).
  Custom range shown as orange HTML vertical lines that update on zoom/pan.
  Floating price label clears immediately on stock/index change.
-->

<script>
    import { onMount, onDestroy } from 'svelte';
    import { createChart, ColorType, AreaSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle } from 'lightweight-charts';

    let {
        data = [],
        currentPeriod = '1y',
        selectMode = false,
        customRange = null,
        onResetPeriod = null,
        onRangeSelect = null,
    } = $props();

    let chartContainer;
    let tooltip;
    let floatingPriceLabel;
    let brushOverlay;
    let rangeLineStart;
    let rangeLineEnd;

    let chart;
    let priceSeries, volumeSeries, ma30Series, ma90Series;
    let observer;

    let processedData = [];
    let lastDataKey = '';
    let isProgrammaticRangeChange = false;
    let activePriceLine = null;
    let lastClosePrice = 0;
    let currentVisibleRange = null;

    let isDragging = false;
    let dragStartX = 0;
    let dragStartRange = null;

    let isBrushing = false;
    let brushStartX = 0;
    let brushStartTime = null;

    const PERIOD_DAYS = {
        '1w': 7, '1mo': 30, '3mo': 90,
        '6mo': 180, '1y': 365, '5y': 1825,
    };

    function formatDate(timeStr) {
        if (!timeStr) return "";
        const [year, month, day] = timeStr.split('-');
        return `${day}/${month}/${year}`;
    }

    function formatVolume(val) {
        if (!val || val === 0) return '—';
        if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
        if (val >= 1000) return (val / 1000).toFixed(1) + 'K';
        return val.toString();
    }

    // --- Immediately hide the floating price label ---
    function clearFloatingLabel() {
        if (floatingPriceLabel) floatingPriceLabel.style.display = 'none';
    }

    // --- VISIBLE RANGE ---

    function applyPeriodRange(period) {
        if (!chart || processedData.length === 0) return;
        isProgrammaticRangeChange = true;
        const timeScale = chart.timeScale();

        if (period === 'max') {
            timeScale.fitContent();
            setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
            return;
        }

        const days = PERIOD_DAYS[period];
        if (!days) {
            timeScale.fitContent();
            setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
            return;
        }

        const lastDate = new Date(processedData[processedData.length - 1].time);
        const cutoff = new Date(lastDate);
        cutoff.setDate(cutoff.getDate() - days);
        const cutoffStr = cutoff.toISOString().split('T')[0];

        const startIdx = processedData.findIndex(d => d.time >= cutoffStr);
        if (startIdx < 0) {
            timeScale.fitContent();
            setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
            return;
        }

        timeScale.setVisibleLogicalRange({ from: startIdx, to: processedData.length - 1 + 3 });
        setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
    }

    function clampRange(from, to) {
        const minIdx = 0;
        const maxIdx = processedData.length - 1 + 3;
        let newFrom = from, newTo = to;
        const size = newTo - newFrom;
        if (newFrom < minIdx) { newFrom = minIdx; newTo = newFrom + size; }
        if (newTo > maxIdx) { newTo = maxIdx; newFrom = newTo - size; }
        if (newFrom < minIdx) newFrom = minIdx;
        return { from: newFrom, to: newTo };
    }

    function xToDate(x) {
        if (!chart) return null;
        const logical = chart.timeScale().coordinateToLogical(x);
        if (logical === null) return null;
        const idx = Math.round(logical);
        if (idx < 0 || idx >= processedData.length) return null;
        return processedData[idx].time;
    }

    // --- ORANGE VERTICAL RANGE LINES ---
    // Positioned via timeToCoordinate so they move with zoom/pan

    function updateRangeLines() {
        if (!rangeLineStart || !rangeLineEnd || !chart) return;

        if (!customRange || !customRange.start || !customRange.end) {
            rangeLineStart.style.display = 'none';
            rangeLineEnd.style.display = 'none';
            return;
        }

        const timeScale = chart.timeScale();

        // Find the closest data point for each boundary
        const startEntry = processedData.find(d => d.time >= customRange.start);
        const endEntries = processedData.filter(d => d.time <= customRange.end);
        const endEntry = endEntries.length > 0 ? endEntries[endEntries.length - 1] : null;

        if (!startEntry || !endEntry) {
            rangeLineStart.style.display = 'none';
            rangeLineEnd.style.display = 'none';
            return;
        }

        const startX = timeScale.timeToCoordinate(startEntry.time);
        const endX = timeScale.timeToCoordinate(endEntry.time);

        if (startX !== null && startX >= 0) {
            rangeLineStart.style.display = 'block';
            rangeLineStart.style.left = `${startX}px`;
        } else {
            rangeLineStart.style.display = 'none';
        }

        if (endX !== null && endX >= 0) {
            rangeLineEnd.style.display = 'block';
            rangeLineEnd.style.left = `${endX}px`;
        } else {
            rangeLineEnd.style.display = 'none';
        }
    }

    // --- SCROLL-WHEEL ZOOM ---

    function handleWheel(event) {
        if (selectMode || !chart || processedData.length === 0) return;
        event.preventDefault();

        const timeScale = chart.timeScale();
        const range = timeScale.getVisibleLogicalRange();
        if (!range) return;

        const rangeSize = range.to - range.from;
        const zoomFactor = event.deltaY > 0 ? 0.15 : -0.15;
        const delta = rangeSize * zoomFactor;

        const rect = chartContainer.getBoundingClientRect();
        const cursorRatio = (event.clientX - rect.left) / rect.width;

        let newFrom = range.from - delta * cursorRatio;
        let newTo = range.to + delta * (1 - cursorRatio);
        if (newTo - newFrom < 3) return;

        const clamped = clampRange(newFrom, newTo);
        timeScale.setVisibleLogicalRange(clamped);
    }

    // --- MOUSE HANDLERS ---

    function handleMouseDown(event) {
        if (!chart || processedData.length === 0 || event.button !== 0) return;
        const rect = chartContainer.getBoundingClientRect();
        const x = event.clientX - rect.left;

        if (selectMode) {
            isBrushing = true;
            brushStartX = x;
            brushStartTime = xToDate(x);
            if (brushOverlay) {
                brushOverlay.style.display = 'block';
                brushOverlay.style.left = `${x}px`;
                brushOverlay.style.width = '0px';
            }
        } else {
            const range = chart.timeScale().getVisibleLogicalRange();
            if (!range) return;
            isDragging = true;
            dragStartX = event.clientX;
            dragStartRange = { ...range };
            chartContainer.style.cursor = 'grabbing';
        }
        event.preventDefault();
    }

    function handleMouseMove(event) {
        const rect = chartContainer.getBoundingClientRect();
        const x = event.clientX - rect.left;

        if (isBrushing && brushOverlay) {
            const left = Math.min(brushStartX, x);
            const width = Math.abs(x - brushStartX);
            brushOverlay.style.left = `${left}px`;
            brushOverlay.style.width = `${width}px`;
            return;
        }

        if (isDragging && dragStartRange) {
            const rangeSize = dragStartRange.to - dragStartRange.from;
            const pixelDelta = event.clientX - dragStartX;
            const barDelta = -(pixelDelta / rect.width) * rangeSize;
            const clamped = clampRange(dragStartRange.from + barDelta, dragStartRange.to + barDelta);
            chart.timeScale().setVisibleLogicalRange(clamped);
        }
    }

    function handleMouseUp(event) {
        if (isBrushing) {
            isBrushing = false;
            const rect = chartContainer.getBoundingClientRect();
            const x = event.clientX - rect.left;
            const endTime = xToDate(x);

            if (brushStartTime && endTime && brushStartTime !== endTime) {
                const start = brushStartTime < endTime ? brushStartTime : endTime;
                const end = brushStartTime < endTime ? endTime : brushStartTime;

                const startIdx = processedData.findIndex(d => d.time >= start);
                const endIdx = processedData.findIndex(d => d.time >= end);
                if (startIdx >= 0 && endIdx >= 0) {
                    isProgrammaticRangeChange = true;
                    chart.timeScale().setVisibleLogicalRange({ from: startIdx, to: endIdx + 3 });
                    setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                }

                if (onRangeSelect) onRangeSelect({ start, end });
            }

            if (brushOverlay) brushOverlay.style.display = 'none';
            return;
        }

        if (isDragging) {
            isDragging = false;
            dragStartRange = null;
            if (chartContainer) chartContainer.style.cursor = selectMode ? 'col-resize' : 'crosshair';
        }
    }

    // --- FLOATING PRICE LABEL ---

    function updateFloatingLabel() {
        if (!floatingPriceLabel || !chart || !priceSeries || lastClosePrice === 0) return;
        try {
            const y = priceSeries.priceToCoordinate(lastClosePrice);
            const chartHeight = chartContainer.clientHeight;
            const isOffScreen = (y === null || y < 0 || y > chartHeight * 0.82);

            if (isOffScreen) {
                floatingPriceLabel.style.display = 'flex';
                if (activePriceLine) activePriceLine.applyOptions({ axisLabelVisible: false });
                if (y === null || y < 0) {
                    floatingPriceLabel.style.top = '8px';
                    floatingPriceLabel.style.bottom = 'auto';
                } else {
                    floatingPriceLabel.style.bottom = `${chartHeight * 0.18}px`;
                    floatingPriceLabel.style.top = 'auto';
                }
                floatingPriceLabel.innerHTML = `
                    <div class="flex items-center gap-2 px-3 py-1.5 bg-white/10 backdrop-blur-md border border-white/20 rounded-lg">
                        <span class="text-[9px] font-bold text-white/50 uppercase tracking-wider">Last Close</span>
                        <span class="text-xs font-black text-white tabular-nums">$${lastClosePrice.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
                    </div>`;
            } else {
                floatingPriceLabel.style.display = 'none';
                if (activePriceLine) activePriceLine.applyOptions({ axisLabelVisible: true });
            }
        } catch(e) {}
    }

    // Cursor + brush cleanup on mode change
    $effect(() => {
        if (chartContainer) chartContainer.style.cursor = selectMode ? 'col-resize' : 'crosshair';
        if (!selectMode && brushOverlay) brushOverlay.style.display = 'none';
    });

    // Update range lines when customRange changes
    $effect(() => {
        const _range = customRange;
        // Small delay to let the chart render first
        setTimeout(() => updateRangeLines(), 30);
    });

    // --- CHART INIT ---

    onMount(() => {
        chart = createChart(chartContainer, {
            layout: {
                background: { type: ColorType.Solid, color: '#0d0d12' },
                textColor: '#94a3b8',
                fontFamily: 'Inter, sans-serif',
            },
            grid: {
                vertLines: { visible: false },
                horzLines: { color: 'rgba(255, 255, 255, 0.03)' },
            },
            rightPriceScale: {
                borderVisible: false,
                scaleMargins: { top: 0.1, bottom: 0.3 },
                visible: true,
            },
            timeScale: {
                borderVisible: false,
                rightOffset: 3,
                barSpacing: 6,
                fixLeftEdge: true,
                fixRightEdge: true,
            },
            crosshair: {
                mode: CrosshairMode.Magnet,
                vertLine: { color: 'rgba(168, 85, 247, 0.4)', labelVisible: false },
                horzLine: { color: 'rgba(168, 85, 247, 0.4)', labelVisible: true },
            },
            handleScroll: false,
            handleScale: false,
        });

        const nuke = () => {
            chartContainer.querySelectorAll('div, a, span').forEach(el => {
                if (el.innerText?.includes('TradingView') || el.href?.includes('tradingview')) el.style.display = 'none';
            });
        };
        nuke();
        observer = new MutationObserver(nuke);
        observer.observe(chartContainer, { childList: true, subtree: true });

        priceSeries = chart.addSeries(AreaSeries, {
            lineColor: '#a855f7',
            topColor: 'rgba(168, 85, 247, 0.3)',
            bottomColor: 'rgba(168, 85, 247, 0)',
            lineWidth: 3,
            lastValueVisible: false,
            priceLineVisible: false,
        });

        ma30Series = chart.addSeries(LineSeries, {
            color: '#22d3ee', lineWidth: 1, lineStyle: LineStyle.Dashed,
            lastValueVisible: false, priceLineVisible: false,
        });
        ma90Series = chart.addSeries(LineSeries, {
            color: '#6366f1', lineWidth: 1, lineStyle: LineStyle.Dashed,
            lastValueVisible: false, priceLineVisible: false,
        });

        volumeSeries = chart.addSeries(HistogramSeries, {
            priceFormat: { type: 'volume' },
            priceScaleId: 'vol',
            lastValueVisible: false,
        });
        chart.priceScale('vol').applyOptions({
            scaleMargins: { top: 0.85, bottom: 0 },
            borderVisible: false,
        });

        // Tooltip
        chart.subscribeCrosshairMove((param) => {
            if (!tooltip || !param.time || !param.point || param.point.x < 0) {
                if (tooltip) tooltip.style.display = 'none';
                return;
            }
            const pData = param.seriesData.get(priceSeries);
            const vData = param.seriesData.get(volumeSeries);
            const m30Data = param.seriesData.get(ma30Series);
            const m90Data = param.seriesData.get(ma90Series);

            if (pData && processedData.length > 0) {
                const livePrice = processedData[processedData.length - 1].close;
                const diff = livePrice - pData.value;
                const diffPct = (diff / pData.value) * 100;
                const colorClass = diff >= 0 ? 'text-green-500' : 'text-red-500';

                tooltip.style.display = 'flex';
                const tooltipWidth = 180, tooltipHeight = 180;
                let left = param.point.x + 20, top = param.point.y + 20;
                if (left + tooltipWidth > chartContainer.clientWidth) left = param.point.x - tooltipWidth - 20;
                if (top + tooltipHeight > chartContainer.clientHeight) top = param.point.y - tooltipHeight - 20;
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
                            <span class="text-[10px] font-black ${colorClass}">${diff >= 0 ? '+' : ''}${diff.toFixed(2)} (${diffPct.toFixed(2)}%)</span>
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
                            <span class="text-xs text-purple-400 font-bold">${vData ? formatVolume(vData.value) : '—'}</span>
                        </div>
                    </div>`;
            }
        });

        chartContainer.addEventListener('wheel', handleWheel, { passive: false });
        chartContainer.addEventListener('mousedown', handleMouseDown);
        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);

        chartContainer.addEventListener('dblclick', () => {
            // If custom range is active, zoom to that range
            if (customRange && customRange.start && customRange.end) {
                const startIdx = processedData.findIndex(d => d.time >= customRange.start);
                const endEntries = processedData.filter(d => d.time <= customRange.end);
                const endIdx = endEntries.length > 0 ? processedData.indexOf(endEntries[endEntries.length - 1]) : -1;
                if (startIdx >= 0 && endIdx >= 0) {
                    isProgrammaticRangeChange = true;
                    chart.timeScale().setVisibleLogicalRange({ from: startIdx, to: endIdx + 3 });
                    setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                }
                return;
            }
            if (onResetPeriod) onResetPeriod();
        });

        // Update floating label + range lines on every visible range change
        chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
            if (range) currentVisibleRange = { ...range };
            updateFloatingLabel();
            updateRangeLines();
        });

        const resizer = new ResizeObserver(() => {
            chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
            if (currentVisibleRange) chart.timeScale().setVisibleLogicalRange(currentVisibleRange);
            updateFloatingLabel();
            updateRangeLines();
        });
        resizer.observe(chartContainer);
    });

    // --- REACTIVE: DATA CHANGED ---
    $effect(() => {
        const raw = $state.snapshot(data);
        if (priceSeries && raw.length > 0) {
            // Immediately clear stale floating label from previous stock
            clearFloatingLabel();

            const uniqueMap = new Map();
            raw.forEach(item => uniqueMap.set(item.time, item));
            processedData = Array.from(uniqueMap.values()).sort((a, b) => new Date(a.time) - new Date(b.time));

            priceSeries.setData(processedData.map(d => ({ time: d.time, value: d.close })));
            ma30Series.setData(processedData.map(d => ({ time: d.time, value: d.ma30 })));
            ma90Series.setData(processedData.map(d => ({ time: d.time, value: d.ma90 })));

            const maxVol = Math.max(...processedData.map(d => d.volume));
            volumeSeries.setData(processedData.map(d => ({
                time: d.time, value: d.volume,
                color: d.volume > (maxVol * 0.8) ? '#a855f7' : 'rgba(168, 85, 247, 0.3)',
            })));

            if (activePriceLine) { try { priceSeries.removePriceLine(activePriceLine); } catch(e) {} }
            lastClosePrice = processedData[processedData.length - 1].close;
            activePriceLine = priceSeries.createPriceLine({
                price: lastClosePrice, color: '#ffffff', lineWidth: 1,
                lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '',
            });

            const dataKey = `${processedData[0]?.time}_${processedData.length}`;
            if (dataKey !== lastDataKey) {
                lastDataKey = dataKey;

                if (currentPeriod) {
                    applyPeriodRange(currentPeriod);
                } else if (customRange && customRange.start && customRange.end) {
                    // Custom range active — zoom to those dates
                    const startIdx = processedData.findIndex(d => d.time >= customRange.start);
                    const endEntries = processedData.filter(d => d.time <= customRange.end);
                    const endIdx = endEntries.length > 0 ? processedData.indexOf(endEntries[endEntries.length - 1]) : -1;
                    if (startIdx >= 0 && endIdx >= 0) {
                        isProgrammaticRangeChange = true;
                        chart.timeScale().setVisibleLogicalRange({ from: startIdx, to: endIdx + 3 });
                        setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                    } else {
                        applyPeriodRange('1y');
                    }
                } else {
                    applyPeriodRange('1y');
                }
            }

            // Re-check floating label after chart has rendered the new data.
            // priceToCoordinate needs a frame to return correct values.
            setTimeout(() => updateFloatingLabel(), 100);
        }
    });

    $effect(() => {
        const period = currentPeriod;
        if (processedData.length > 0 && period) {
            applyPeriodRange(period);
            // Re-check after the range change settles
            setTimeout(() => updateFloatingLabel(), 100);
        }
    });

    onDestroy(() => {
        chartContainer?.removeEventListener('wheel', handleWheel);
        chartContainer?.removeEventListener('mousedown', handleMouseDown);
        window?.removeEventListener('mousemove', handleMouseMove);
        window?.removeEventListener('mouseup', handleMouseUp);
        observer?.disconnect();
        chart?.remove();
    });
</script>

<div bind:this={chartContainer} class="w-full h-full min-h-[500px] relative rounded-2xl overflow-hidden bg-[#0d0d12]" style="transition: none !important; cursor: crosshair;">

    <!-- Legend -->
    <div class="absolute top-4 left-6 z-[110] flex gap-5 pointer-events-none p-2 bg-black/20 backdrop-blur-sm rounded-lg">
        <div class="flex items-center gap-2"><div class="w-4 h-[2px] bg-[#a855f7]"></div><span class="text-[9px] text-white/60 uppercase font-black tracking-widest">Price</span></div>
        <div class="flex items-center gap-2"><div class="w-4 h-0 border-t-2 border-dashed border-white"></div><span class="text-[9px] text-white font-black tracking-widest">Live</span></div>
        <div class="flex items-center gap-2"><div class="w-4 h-0 border-t border-dashed border-cyan-400"></div><span class="text-[9px] text-cyan-400/80 uppercase font-black tracking-widest">MA 30</span></div>
        <div class="flex items-center gap-2"><div class="w-4 h-0 border-t border-dashed border-indigo-400"></div><span class="text-[9px] text-indigo-400/80 uppercase font-black tracking-widest">MA 90</span></div>
        <div class="flex items-center gap-2"><div class="w-4 h-0 border-t-2 border-dotted border-[#a855f7]/60"></div><span class="text-[9px] text-white/30 uppercase font-black tracking-widest">Volume</span></div>
    </div>

    {#if selectMode}
        <div class="absolute top-4 right-6 z-[110] px-3 py-1.5 bg-orange-500/20 border border-orange-500/40 rounded-lg pointer-events-none">
            <span class="text-[10px] font-black text-orange-400 uppercase tracking-wider">Drag to select range</span>
        </div>
    {/if}

    <!-- Brush overlay -->
    <div bind:this={brushOverlay} class="absolute top-0 bottom-0 z-[105] hidden pointer-events-none bg-purple-500/15 border-l-2 border-r-2 border-purple-500/50"></div>

    <!-- Custom range vertical lines (dashed orange) -->
    <div bind:this={rangeLineStart} class="absolute top-0 bottom-0 z-[106] pointer-events-none hidden" style="width: 0; border-left: 2px dashed rgba(249, 115, 22, 0.6);"></div>
    <div bind:this={rangeLineEnd} class="absolute top-0 bottom-0 z-[106] pointer-events-none hidden" style="width: 0; border-left: 2px dashed rgba(249, 115, 22, 0.6);"></div>

    <div bind:this={tooltip} class="absolute hidden z-[120] pointer-events-none transition-all duration-75"></div>
    <div bind:this={floatingPriceLabel} class="absolute right-2 z-[115] pointer-events-none hidden"></div>
</div>

<style>
    :global(.tv-lightweight-charts-attribution) { display: none !important; }
</style>