<!--
  Chart Component
  ===============
  Candlestick + faint area overlay. Currency symbol passed as prop.
  Sticky price label pins to top/bottom when off-screen.
-->

<script>
    import { onMount, onDestroy, tick } from 'svelte';
    import { createChart, ColorType, CandlestickSeries, AreaSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle } from 'lightweight-charts';

    let {
        data = [],
        currentPeriod = '1y',
        selectMode = false,
        customRange = null,
        currency = '$',
        onResetPeriod = null,
        onRangeSelect = null,
        compareData = null,
        compareColors = null,
        compareNames = null,
        hideVolume = false,
    } = $props();

    let chartContainer;
    let tooltip;
    let brushOverlay;
    let rangeLineStart;
    let rangeLineEnd;
    let stickyLabel;

    let chart;
    let candleSeries, areaSeries, volumeSeries, ma30Series, ma90Series;
    let comparisonSeries = []; // Array of { symbol, series } for comparison mode — NEVER reassign, only mutate (splice/push) so closure in subscribeCrosshairMove sees updates
    let legendSeries = $state([]); // Reactive copy updated only after series are ready — avoids glitch
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

        // Use time-based range (works correctly with multiple series that have different time points)
        if (comparisonSeries.length > 0) {
            timeScale.setVisibleRange({ from: cutoffStr, to: processedData[processedData.length - 1].time });
        } else {
            // Stock mode: logical indices match processedData perfectly
            const startIdx = processedData.findIndex(d => d.time >= cutoffStr);
            if (startIdx < 0) {
                timeScale.fitContent();
                setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                return;
            }
            timeScale.setVisibleLogicalRange({ from: startIdx, to: processedData.length - 1 + 3 });
        }
        setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
    }

    function clampRange(from, to) {
        // In comparison mode, let the chart handle its own bounds
        // because logical indices don't map 1:1 to processedData
        if (comparisonSeries.length > 0) {
            return { from, to };
        }
        const minIdx = 0;
        const maxIdx = processedData.length - 1 + 3;
        let newFrom = from, newTo = to;
        const size = newTo - newFrom;
        if (newFrom < minIdx) { newFrom = minIdx; newTo = newFrom + size; }
        if (newTo > maxIdx) { newTo = maxIdx; newFrom = newTo - size; }
        if (newFrom < minIdx) newFrom = minIdx;
        return { from: newFrom, to: newTo };
    }

    // Merged time axis for comparison mode (all unique dates from all series, sorted)
    let mergedTimeAxis = [];

    function buildMergedTimeAxis(cData) {
        if (!cData || !cData.series || cData.series.length === 0) {
            mergedTimeAxis = [];
            return;
        }
        const timeSet = new Set();
        for (const s of cData.series) {
            for (const p of s.points) timeSet.add(p.time);
        }
        mergedTimeAxis = Array.from(timeSet).sort();
    }

    function xToDate(x) {
        if (!chart) return null;
        const timeScale = chart.timeScale();
        const logical = timeScale.coordinateToLogical(x);
        if (logical === null) return null;
        const idx = Math.round(logical);

        // In comparison mode, use the merged time axis (matches chart's internal bar indices)
        if (comparisonSeries.length > 0 && mergedTimeAxis.length > 0) {
            if (idx < 0) return mergedTimeAxis[0];
            if (idx >= mergedTimeAxis.length) return mergedTimeAxis[mergedTimeAxis.length - 1];
            return mergedTimeAxis[idx];
        }

        // Stock mode: processedData indices match 1:1
        if (idx < 0 || idx >= processedData.length) return null;
        return processedData[idx].time;
    }

    // --- ORANGE RANGE LINES ---

    function updateRangeLines() {
        if (!rangeLineStart || !rangeLineEnd || !chart) return;
        if (!customRange || !customRange.start || !customRange.end) {
            rangeLineStart.style.display = 'none';
            rangeLineEnd.style.display = 'none';
            return;
        }

        const timeScale = chart.timeScale();
        // Use mergedTimeAxis in comparison mode for accurate time lookup
        const timeSource = (comparisonSeries.length > 0 && mergedTimeAxis.length > 0)
            ? mergedTimeAxis.map(t => ({ time: t }))
            : processedData;

        const startEntry = timeSource.find(d => d.time >= customRange.start);
        const endEntries = timeSource.filter(d => d.time <= customRange.end);
        const endEntry = endEntries.length > 0 ? endEntries[endEntries.length - 1] : null;

        if (!startEntry || !endEntry) {
            rangeLineStart.style.display = 'none';
            rangeLineEnd.style.display = 'none';
            return;
        }

        const startX = timeScale.timeToCoordinate(startEntry.time);
        const endX = timeScale.timeToCoordinate(endEntry.time);

        rangeLineStart.style.display = (startX !== null && startX >= 0) ? 'block' : 'none';
        if (startX !== null) rangeLineStart.style.left = `${startX}px`;
        rangeLineEnd.style.display = (endX !== null && endX >= 0) ? 'block' : 'none';
        if (endX !== null) rangeLineEnd.style.left = `${endX}px`;
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

                if (comparisonSeries.length > 0) {
                    // Comparison mode: use time-based range
                    isProgrammaticRangeChange = true;
                    chart.timeScale().setVisibleRange({ from: start, to: end });
                    setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                } else {
                    // Stock mode: use logical indices
                    const startIdx = processedData.findIndex(d => d.time >= start);
                    const endEntries = processedData.filter(d => d.time <= end);
                    const endIdx = endEntries.length > 0 ? processedData.indexOf(endEntries[endEntries.length - 1]) : -1;
                    if (startIdx >= 0 && endIdx >= 0) {
                        isProgrammaticRangeChange = true;
                        chart.timeScale().setVisibleLogicalRange({ from: startIdx, to: endIdx + 3 });
                        setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                    }
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

    // --- PRICE LINE VISIBILITY ---

    function updatePriceLineVisibility() {
        if (!chart || !candleSeries || lastClosePrice === 0 || !stickyLabel) return;
        try {
            const y = candleSeries.priceToCoordinate(lastClosePrice);
            const chartHeight = chartContainer.clientHeight;
            const priceAreaTop = chartHeight * 0.05;
            const priceAreaBottom = chartHeight * 0.82;
            const isVisible = (y !== null && y >= priceAreaTop && y <= priceAreaBottom);

            if (isVisible) {
                stickyLabel.style.display = 'none';
                if (activePriceLine) activePriceLine.applyOptions({ axisLabelVisible: true });
            } else {
                if (activePriceLine) activePriceLine.applyOptions({ axisLabelVisible: false });
                stickyLabel.style.display = 'block';
                const priceText = lastClosePrice.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
                stickyLabel.textContent = priceText;

                if (y === null || y < priceAreaTop) {
                    stickyLabel.style.top = `${priceAreaTop}px`;
                    stickyLabel.style.bottom = 'auto';
                } else {
                    stickyLabel.style.top = `${priceAreaBottom - 14}px`;
                    stickyLabel.style.bottom = 'auto';
                }
            }
        } catch(e) {}
    }

    // Cursor + brush cleanup
    $effect(() => {
        if (chartContainer) chartContainer.style.cursor = selectMode ? 'col-resize' : 'crosshair';
        if (!selectMode && brushOverlay) brushOverlay.style.display = 'none';
    });

    $effect(() => {
        const _range = customRange;
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
                scaleMargins: { top: 0.1, bottom: 0.1 },
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

        candleSeries = chart.addSeries(CandlestickSeries, {
            upColor: 'rgba(34, 197, 94, 0.6)',
            downColor: 'rgba(239, 68, 68, 0.6)',
            borderUpColor: 'rgba(34, 197, 94, 0.8)',
            borderDownColor: 'rgba(239, 68, 68, 0.8)',
            wickUpColor: 'rgba(34, 197, 94, 0.5)',
            wickDownColor: 'rgba(239, 68, 68, 0.5)',
            lastValueVisible: false,
            priceLineVisible: false,
        });

        areaSeries = chart.addSeries(AreaSeries, {
            lineColor: 'rgba(168, 85, 247, 0.35)',
            topColor: 'rgba(168, 85, 247, 0.08)',
            bottomColor: 'rgba(168, 85, 247, 0)',
            lineWidth: 1,
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
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
            lastValueVisible: true,
        });
        chart.priceScale('vol').applyOptions({
            scaleMargins: { top: 0.85, bottom: 0 },
            borderVisible: false,
        });

        // --- TOOLTIP ---
        chart.subscribeCrosshairMove((param) => {
            if (!tooltip || !param.time || !param.point || param.point.x < 0) {
                if (tooltip) { tooltip.style.display = 'none'; tooltip.style.visibility = 'visible'; }
                return;
            }

            // --- COMPARISON MODE TOOLTIP ---
            if (comparisonSeries.length > 0) {
                let rows = '';
                let hasAnyData = false;
                for (const cs of comparisonSeries) {
                    const sData = param.seriesData?.get(cs.series);
                    // For missing data points (sparse series), carry forward last known value
                    let pct = sData?.value;
                    if (pct === undefined && compareData?.series) {
                        const s = compareData.series.find(s => s.symbol === cs.symbol);
                        if (s) {
                            const timeStr = String(param.time);
                            const pts = s.points;
                            let last = undefined;
                            for (let i = 0; i < pts.length; i++) {
                                if (pts[i].time <= timeStr) last = pts[i].pct;
                                else break;
                            }
                            pct = last;
                        }
                    }
                    if (pct === undefined) continue;
                    hasAnyData = true;
                    const color = cs.color || COMPARE_COLORS[cs.symbol] || '#8b5cf6';
                    const name = cs.name || COMPARE_NAMES[cs.symbol] || cs.symbol;
                    const sign = pct >= 0 ? '+' : '';
                    const pctColor = pct >= 0 ? 'rgba(34,197,94,0.9)' : 'rgba(239,68,68,0.8)';
                    // Find raw close from compareData
                    let rawClose = '';
                    if (compareData && compareData.series) {
                        const s = compareData.series.find(s => s.symbol === cs.symbol);
                        if (s) {
                            const pt = s.points.find(p => p.time === param.time);
                            if (pt && pt.close != null) rawClose = pt.close.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1});
                        }
                    }
                    rows += `
                        <div class="flex items-center gap-2">
                            <div style="width:8px;height:3px;border-radius:2px;background:${color};flex-shrink:0"></div>
                            <span class="text-[9px] uppercase font-bold" style="color:${color};min-width:55px">${name}</span>
                            <span class="text-[11px] font-black tabular-nums ml-auto" style="color:${pctColor}">${sign}${pct.toFixed(2)}%</span>
                        </div>
                        ${rawClose ? `<div class="flex justify-end"><span class="text-[9px] text-white/30 font-mono tabular-nums">${rawClose}</span></div>` : ''}
                    `;
                }

                // Aggregated volume for tooltip
                let volTotal = 0;
                if (compareData && compareData.series) {
                    const timeStr = String(param.time);
                    for (const s of compareData.series) {
                        const pt = s.points.find(p => p.time === timeStr);
                        if (pt && pt.volume) volTotal += pt.volume;
                    }
                }

                if (!hasAnyData) {
                    tooltip.style.display = 'none';
                    return;
                }

                // Set HTML first so we can measure actual height
                const tooltipWidth = 200;
                tooltip.style.visibility = 'hidden';
                tooltip.style.display = 'flex';

                tooltip.innerHTML = `
                    <div class="flex flex-col p-3 bg-[#16161e]/95 border border-white/10 rounded-xl shadow-2xl backdrop-blur-md min-w-[180px] gap-1.5 pointer-events-none">
                        <span class="text-[9px] text-white/30 uppercase font-black tracking-widest border-b border-white/5 pb-1 mb-1">${formatDate(param.time)}</span>
                        ${rows}
                        ${volTotal > 0 ? `<div class="flex justify-between items-center gap-4 pt-1 border-t border-white/5 mt-1"><span class="text-[9px] text-purple-400/60 uppercase font-bold">Vol</span><span class="text-xs text-purple-400 font-bold">${formatVolume(volTotal)}</span></div>` : ''}
                    </div>`;
                // Position after render so we know actual height
                { const tw = tooltipWidth, th = tooltip.offsetHeight || 200;
                  const cw = chartContainer.clientWidth, ch = chartContainer.clientHeight;
                  let left = param.point.x + 20, top = param.point.y + 20;
                  if (left + tw > cw) left = param.point.x - tw - 20;
                  if (top + th > ch) top = Math.max(4, ch - th - 4);
                  left = Math.max(4, Math.min(left, cw - tw - 4));
                  tooltip.style.left = `${left}px`;
                  tooltip.style.top = `${top}px`;
                  tooltip.style.visibility = 'visible'; }
                return;
            }

            // --- STOCK MODE TOOLTIP ---
            const pData = param.seriesData.get(candleSeries);
            const vData = param.seriesData.get(volumeSeries);
            const m30Data = param.seriesData.get(ma30Series);
            const m90Data = param.seriesData.get(ma90Series);
            const c = currency;

            if (pData && processedData.length > 0) {
                const closePrice = pData.close;
                const openPrice = pData.open;
                const highPrice = pData.high;
                const lowPrice = pData.low;
                const livePrice = processedData[processedData.length - 1].close;
                const diff = livePrice - closePrice;
                const diffPct = (diff / closePrice) * 100;
                const colorClass = diff >= 0 ? 'text-green-500' : 'text-red-500';
                const candleUp = closePrice >= openPrice;

                tooltip.style.display = 'flex';
                const tooltipWidth = 190, tooltipHeight = 270;
                let left = param.point.x + 20, top = param.point.y + 20;
                if (left + tooltipWidth > chartContainer.clientWidth) left = param.point.x - tooltipWidth - 20;
                if (top + tooltipHeight > chartContainer.clientHeight) top = param.point.y - tooltipHeight - 20;
                tooltip.style.left = `${left}px`;
                tooltip.style.top = `${top}px`;

                tooltip.innerHTML = `
                    <div class="flex flex-col p-3 bg-[#16161e]/95 border border-white/10 rounded-xl shadow-2xl backdrop-blur-md min-w-[170px] gap-1.5 pointer-events-none">
                        <span class="text-[9px] text-white/30 uppercase font-black tracking-widest border-b border-white/5 pb-1 mb-1">${formatDate(param.time)}</span>
                        
                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[9px] text-white/40 uppercase font-bold">Open</span>
                            <span class="text-xs text-white/70 font-black">${c}${openPrice.toFixed(2)}</span>
                        </div>
                        
                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[9px] text-white/40 uppercase font-bold">High</span>
                            <span class="text-xs text-white/70 font-black">${c}${highPrice.toFixed(2)}</span>
                        </div>
                        
                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[9px] text-white/40 uppercase font-bold">Low</span>
                            <span class="text-xs text-white/70 font-black">${c}${lowPrice.toFixed(2)}</span>
                        </div>
                        
                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[9px] text-white/60 uppercase font-bold">Close</span>
                            <span class="text-xs font-black" style="color: ${candleUp ? 'rgba(34,197,94,0.9)' : 'rgba(239,68,68,0.8)'}">${c}${closePrice.toFixed(2)}</span>
                        </div>

                        <div class="flex justify-between items-center gap-4 border-t border-white/5 pt-1 mt-1">
                            <span class="text-[9px] text-white/40 uppercase font-bold">vs Live</span>
                            <span class="text-[10px] font-black ${colorClass}">${diff >= 0 ? '+' : ''}${diff.toFixed(2)} (${diffPct.toFixed(2)}%)</span>
                        </div>
                        
                        <div class="flex justify-between items-center gap-4 border-t border-white/5 pt-1 mt-1">
                            <span class="text-[9px] text-cyan-400/60 uppercase font-bold">MA 30</span>
                            <span class="text-xs text-cyan-400 font-bold">${m30Data ? c + m30Data.value.toFixed(2) : '—'}</span>
                        </div>
                        
                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[9px] text-indigo-400/60 uppercase font-bold">MA 90</span>
                            <span class="text-xs text-indigo-400 font-bold">${m90Data ? c + m90Data.value.toFixed(2) : '—'}</span>
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
            if (customRange && customRange.start && customRange.end) {
                isProgrammaticRangeChange = true;
                if (comparisonSeries.length > 0) {
                    chart.timeScale().setVisibleRange({ from: customRange.start, to: customRange.end });
                } else {
                    const startIdx = processedData.findIndex(d => d.time >= customRange.start);
                    const endEntries = processedData.filter(d => d.time <= customRange.end);
                    const endIdx = endEntries.length > 0 ? processedData.indexOf(endEntries[endEntries.length - 1]) : -1;
                    if (startIdx >= 0 && endIdx >= 0) {
                        chart.timeScale().setVisibleLogicalRange({ from: startIdx, to: endIdx + 3 });
                    }
                }
                setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                return;
            }
            if (onResetPeriod) onResetPeriod();
        });

        chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
            if (range) currentVisibleRange = { ...range };
            updatePriceLineVisibility();
            updateRangeLines();
        });

        const resizer = new ResizeObserver(() => {
            chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
            if (currentVisibleRange) chart.timeScale().setVisibleLogicalRange(currentVisibleRange);
            updatePriceLineVisibility();
            updateRangeLines();
        });
        resizer.observe(chartContainer);
    });

    // --- REACTIVE: DATA ---
    $effect(() => {
        const raw = $state.snapshot(data);
        if (candleSeries && raw.length > 0) {
            const uniqueMap = new Map();
            raw.forEach(item => uniqueMap.set(item.time, item));
            processedData = Array.from(uniqueMap.values()).sort((a, b) => new Date(a.time) - new Date(b.time));

            candleSeries.setData(processedData.map(d => ({
                time: d.time, open: d.open || d.close, high: d.high || d.close,
                low: d.low || d.close, close: d.close,
            })));
            areaSeries.setData(processedData.map(d => ({ time: d.time, value: d.close })));
            ma30Series.setData(processedData.map(d => ({ time: d.time, value: d.ma30 })));
            ma90Series.setData(processedData.map(d => ({ time: d.time, value: d.ma90 })));

            const maxVol = Math.max(...processedData.map(d => d.volume));
            volumeSeries.setData(processedData.map(d => ({
                time: d.time, value: d.volume,
                color: d.volume > (maxVol * 0.8) ? '#a855f7' : 'rgba(168, 85, 247, 0.3)',
            })));

            if (activePriceLine) { try { candleSeries.removePriceLine(activePriceLine); } catch(e) {} }
            lastClosePrice = processedData[processedData.length - 1].close;
            activePriceLine = candleSeries.createPriceLine({
                price: lastClosePrice, color: '#ffffff', lineWidth: 1,
                lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '',
            });

            const dataKey = `${processedData[0]?.time}_${processedData.length}`;
            if (dataKey !== lastDataKey) {
                lastDataKey = dataKey;
                if (currentPeriod) {
                    applyPeriodRange(currentPeriod);
                } else if (customRange && customRange.start && customRange.end) {
                    const startIdx = processedData.findIndex(d => d.time >= customRange.start);
                    const endEntries = processedData.filter(d => d.time <= customRange.end);
                    const endIdx = endEntries.length > 0 ? processedData.indexOf(endEntries[endEntries.length - 1]) : -1;
                    if (startIdx >= 0 && endIdx >= 0) {
                        isProgrammaticRangeChange = true;
                        chart.timeScale().setVisibleLogicalRange({ from: startIdx, to: endIdx + 3 });
                        setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                    } else { applyPeriodRange('1y'); }
                } else { applyPeriodRange('1y'); }
            }
            setTimeout(() => updatePriceLineVisibility(), 100);
        }
    });

    $effect(() => {
        const period = currentPeriod;
        if (processedData.length > 0 && period) {
            applyPeriodRange(period);
            setTimeout(() => updatePriceLineVisibility(), 100);
        }
    });

    // --- REACTIVE: COMPARISON DATA ---
    const _DEFAULT_COLORS = {
        '^GSPC':     '#e2e8f0',
        '^STOXX50E': '#2563eb',
        '^FTSE':     '#ec4899',
        '^N225':     '#f59e0b',
        '000300.SS': '#ef4444',
        '^NSEI':     '#22c55e',
    };
    const _DEFAULT_NAMES = {
        '^GSPC':     'S&P 500',
        '^STOXX50E': 'STOXX 50',
        '^FTSE':     'FTSE 100',
        '^N225':     'Nikkei 225',
        '000300.SS': 'CSI 300',
        '^NSEI':     'Nifty 50',
    };
    let COMPARE_COLORS = $derived(compareColors || _DEFAULT_COLORS);
    let COMPARE_NAMES = $derived(compareNames || _DEFAULT_NAMES);

    let _lastCompareSymbols = '';

    function setComparisonVolume(cData) {
        if (hideVolume) {
            volumeSeries.setData([]);
            return;
        }
        // Aggregate volume across all selected indices per date
        const volMap = new Map();
        for (const s of cData.series) {
            for (const p of s.points) {
                volMap.set(p.time, (volMap.get(p.time) || 0) + (p.volume || 0));
            }
        }
        const volData = Array.from(volMap.entries())
            .sort((a, b) => a[0].localeCompare(b[0]));
        const maxVol = Math.max(...volData.map(d => d[1]), 1);
        volumeSeries.setData(volData.map(([time, vol]) => ({
            time,
            value: vol,
            color: vol > (maxVol * 0.8) ? '#a855f7' : 'rgba(168, 85, 247, 0.3)',
        })));
    }

    function renderComparisonFull(cData) {
        if (!chart) return;

        // Hide stock-mode series (except volumeSeries — reused for aggregated volume)
        if (candleSeries) {
            try {
                candleSeries.setData([]);
                areaSeries.setData([]);
                ma30Series.setData([]);
                ma90Series.setData([]);
                if (activePriceLine) {
                    try { candleSeries.removePriceLine(activePriceLine); } catch(e) {}
                    activePriceLine = null;
                }
            } catch(e) {}
        }

        // Remove old comparison series
        comparisonSeries.forEach(cs => {
            try { chart.removeSeries(cs.series); } catch(e) {}
        });
        comparisonSeries.splice(0);

        // Create all series
        cData.series.forEach((s) => {
            const color = COMPARE_COLORS[s.symbol] || '#8b5cf6';
            const name = COMPARE_NAMES[s.symbol] || s.symbol;
            const lineSeries = chart.addSeries(LineSeries, {
                color: color,
                lineWidth: 1,
                title: '',
                lastValueVisible: false,
                priceLineVisible: false,
                priceFormat: { type: 'custom', formatter: (p) => p.toFixed(1) + '%' },
                crosshairMarkerVisible: true,
                crosshairMarkerRadius: 4,
            });
            lineSeries.setData(s.points.map(p => ({ time: p.time, value: p.pct })));

            const lastPct = s.points[s.points.length - 1]?.pct ?? 0;
            lineSeries.createPriceLine({
                price: lastPct, color: color, lineWidth: 0,
                lineStyle: LineStyle.Solid, axisLabelVisible: true,
                title: '', lineVisible: false,
            });

            comparisonSeries.push({ symbol: s.symbol, series: lineSeries, color, name });
        });

        // Zero line
        if (comparisonSeries.length > 0) {
            try {
                comparisonSeries[0].series.createPriceLine({
                    price: 0, color: 'rgba(255,255,255,0.15)', lineWidth: 1,
                    lineStyle: LineStyle.Dashed, axisLabelVisible: false,
                });
            } catch(e) {}
        }

        // Populate processedData from longest series
        let longest = cData.series[0].points;
        for (const s of cData.series) {
            if (s.points.length > longest.length) longest = s.points;
        }
        processedData = longest.map(p => ({ time: p.time, close: p.pct }));

        // Build merged time axis for correct xToDate mapping
        buildMergedTimeAxis(cData);

        // Set aggregated volume histogram
        setComparisonVolume(cData);

        // Apply saved period or custom range (same as stock mode on refresh)
        if (currentPeriod) {
            applyPeriodRange(currentPeriod);
        } else if (customRange && customRange.start && customRange.end) {
            chart.timeScale().setVisibleRange({ from: customRange.start, to: customRange.end });
        } else {
            applyPeriodRange('1y');
        }

        // Update reactive legend AFTER all series rendered — fully self-contained, no prop deps
        legendSeries = comparisonSeries.map(cs => ({
            symbol: cs.symbol,
            color: COMPARE_COLORS[cs.symbol] || '#8b5cf6',
            name: COMPARE_NAMES[cs.symbol] || cs.symbol
        }));
    }

    function updateComparisonSeries(cData) {
        if (!chart) return;

        // Save the current visible TIME range before any modifications
        let savedFrom = null, savedTo = null;
        try {
            const vr = chart.timeScale().getVisibleRange();
            if (vr) { savedFrom = vr.from; savedTo = vr.to; }
        } catch(e) {}

        const currentSymbols = new Set(comparisonSeries.map(cs => cs.symbol));
        const newSymbols = new Set(cData.series.map(s => s.symbol));

        // Remove series that are no longer selected
        const toRemove = comparisonSeries.filter(cs => !newSymbols.has(cs.symbol));
        toRemove.forEach(cs => {
            try { chart.removeSeries(cs.series); } catch(e) {}
        });
        // Mutate in-place: remove items not in newSymbols
        for (let i = comparisonSeries.length - 1; i >= 0; i--) {
            if (!newSymbols.has(comparisonSeries[i].symbol)) {
                comparisonSeries.splice(i, 1);
            }
        }

        // Add new series
        cData.series.forEach((s) => {
            if (currentSymbols.has(s.symbol)) return; // already shown
            const color = COMPARE_COLORS[s.symbol] || '#8b5cf6';
            const name = COMPARE_NAMES[s.symbol] || s.symbol;
            const lineSeries = chart.addSeries(LineSeries, {
                color: color,
                lineWidth: 1,
                title: '',
                lastValueVisible: false,
                priceLineVisible: false,
                priceFormat: { type: 'custom', formatter: (p) => p.toFixed(1) + '%' },
                crosshairMarkerVisible: true,
                crosshairMarkerRadius: 4,
            });
            lineSeries.setData(s.points.map(p => ({ time: p.time, value: p.pct })));

            const lastPct = s.points[s.points.length - 1]?.pct ?? 0;
            lineSeries.createPriceLine({
                price: lastPct, color: color, lineWidth: 0,
                lineStyle: LineStyle.Solid, axisLabelVisible: true,
                title: '', lineVisible: false,
            });

            comparisonSeries.push({ symbol: s.symbol, series: lineSeries, color, name });
        });

        // Update processedData from longest series (in case it changed)
        let longest = cData.series[0].points;
        for (const s of cData.series) {
            if (s.points.length > longest.length) longest = s.points;
        }
        processedData = longest.map(p => ({ time: p.time, close: p.pct }));

        // Rebuild merged time axis
        buildMergedTimeAxis(cData);

        // Update aggregated volume
        setComparisonVolume(cData);

        // Sync reactive legend — fully self-contained
        legendSeries = comparisonSeries.map(cs => ({
            symbol: cs.symbol,
            color: COMPARE_COLORS[cs.symbol] || '#8b5cf6',
            name: COMPARE_NAMES[cs.symbol] || cs.symbol
        }));

        // Restore the exact same visible time range to prevent drift
        if (savedFrom && savedTo) {
            isProgrammaticRangeChange = true;
            chart.timeScale().setVisibleRange({ from: savedFrom, to: savedTo });
            setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
        }
    }

    let _lastCompareDataVersion = 0;

    $effect(async () => {
        const cData = compareData;
        if (!cData || !cData.series || cData.series.length === 0) {
            if (chart) {
                comparisonSeries.forEach(cs => {
                    try { chart.removeSeries(cs.series); } catch(e) {}
                });
            }
            comparisonSeries.splice(0);
            mergedTimeAxis = [];
            _lastCompareSymbols = '';
            _lastCompareDataVersion = 0;
            return;
        }

        const newSymbols = cData.series.map(s => s.symbol).sort().join(',');
        const dataVersion = cData._version || 0;

        if (newSymbols === _lastCompareSymbols && comparisonSeries.length > 0) {
            // Same symbols — check if data actually changed
            if (dataVersion === _lastCompareDataVersion && dataVersion > 0) return;
            _lastCompareDataVersion = dataVersion;
            _lastCompareSymbols = newSymbols;
            // Data changed but symbols same — just update series data in-place
            comparisonSeries.forEach(cs => {
                const s = cData.series.find(s => s.symbol === cs.symbol);
                if (s) cs.series.setData(s.points.map(p => ({ time: p.time, value: p.pct })));
            });
            return;
        }

        const isFirstLoad = _lastCompareSymbols === '';
        const prevSymbolSet = new Set(_lastCompareSymbols.split(',').filter(Boolean));
        const nextSymbolSet = new Set(newSymbols.split(',').filter(Boolean));
        const isCompleteChange = isFirstLoad || [...prevSymbolSet].every(s => !nextSymbolSet.has(s));
        _lastCompareSymbols = newSymbols;
        _lastCompareDataVersion = dataVersion;

        legendSeries = [];
        if (isCompleteChange) {
            // Mode switch or first load — full atomic render
            await tick();
            renderComparisonFull(cData);
        } else {
            // Incremental index add/remove — use updateComparisonSeries to keep view stable
            updateComparisonSeries(cData);
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
    const SECTOR_ABBREV = {
        'Technology': 'Tech',
        'Financial Services': 'Financials',
        'Healthcare': 'Health',
        'Industrials': 'Industls',
        'Consumer Cyclical': 'Cons Cyc',
        'Communication Services': 'Comms',
        'Consumer Defensive': 'Cons Def',
        'Basic Materials': 'Materials',
        'Real Estate': 'Real Est',
        'Energy': 'Energy',
        'Utilities': 'Utilities'
    };
    function abbrevSector(name) { return SECTOR_ABBREV[name] || name; }
</script>

<div bind:this={chartContainer} class="w-full h-full relative rounded-2xl overflow-hidden bg-[#0d0d12]" style="min-height:clamp(220px,35vh,600px);transition:none !important;cursor:crosshair">

    {#if compareData && compareData.series && compareData.series.length > 0}
        <!-- Comparison mode legend -->
        <div class="absolute top-4 left-6 z-[110] flex gap-4 pointer-events-none p-2 bg-black/20 backdrop-blur-sm rounded-lg flex-wrap">
            {#each legendSeries as s}
                <div class="flex items-center gap-1.5">
                    <div class="w-4 h-[2px] rounded-full" style="background: {s.color}"></div>
                    <span class="text-[9px] uppercase font-black tracking-widest" style="color: {s.color}">{s.name}</span>
                </div>
            {/each}
        </div>
    {:else}
        <!-- Stock mode legend -->
        <div class="absolute top-4 left-6 z-[110] flex gap-5 pointer-events-none p-2 bg-black/20 backdrop-blur-sm rounded-lg">
            <div class="flex items-center gap-1.5"><div class="w-2 h-3 bg-green-500/60 rounded-[1px]"></div><div class="w-2 h-3 bg-red-500/60 rounded-[1px]"></div><span class="text-[9px] text-white/60 uppercase font-black tracking-widest ml-1">OHLC</span></div>
            <div class="flex items-center gap-2"><div class="w-4 h-0 border-t-2 border-dashed border-white"></div><span class="text-[9px] text-white font-black tracking-widest">Live</span></div>
            <div class="flex items-center gap-2"><div class="w-4 h-0 border-t border-dashed border-cyan-400"></div><span class="text-[9px] text-cyan-400/80 uppercase font-black tracking-widest">MA 30</span></div>
            <div class="flex items-center gap-2"><div class="w-4 h-0 border-t border-dashed border-indigo-400"></div><span class="text-[9px] text-indigo-400/80 uppercase font-black tracking-widest">MA 90</span></div>
            <div class="flex items-center gap-2"><div class="w-4 h-0 border-t-2 border-dotted border-[#a855f7]/60"></div><span class="text-[9px] text-[#a855f7]/60 uppercase font-black tracking-widest">Volume</span></div>
        </div>
    {/if}

    {#if selectMode}
        <div class="absolute top-4 right-6 z-[110] px-3 py-1.5 bg-orange-500/20 border border-orange-500/40 rounded-lg pointer-events-none">
            <span class="text-[10px] font-black text-orange-400 uppercase tracking-wider">Drag to select range</span>
        </div>
    {/if}

    <div bind:this={brushOverlay} class="absolute top-0 bottom-0 z-[105] hidden pointer-events-none bg-orange-500/20 border-l-2 border-r-2 border-orange-500/50"></div>
    <div bind:this={rangeLineStart} class="absolute top-0 bottom-0 z-[106] pointer-events-none hidden" style="width: 0; border-left: 2px dashed rgba(249, 115, 22, 0.6);"></div>
    <div bind:this={rangeLineEnd} class="absolute top-0 bottom-0 z-[106] pointer-events-none hidden" style="width: 0; border-left: 2px dashed rgba(249, 115, 22, 0.6);"></div>
    <div bind:this={tooltip} class="absolute hidden z-[120] pointer-events-none transition-all duration-75"></div>
    <div bind:this={stickyLabel} class="absolute right-0 z-[115] pointer-events-none hidden text-[11px] font-mono font-bold tabular-nums text-white bg-white/20 px-1.5 py-0.5 rounded-l" style="backdrop-filter: blur(4px);"></div>
</div>

<style>
    :global(.tv-lightweight-charts-attribution) { display: none !important; }
</style>