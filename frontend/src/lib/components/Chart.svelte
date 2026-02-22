<!-- candlestick chart with comparison line overlay using lightweight-charts.
     handles stock mode (OHLC+MA+volume), comparison mode (% return lines),
     scroll/zoom, brush selection, tooltips, and visible range management. -->

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

    // --- DOM REFS ---

    let chartContainer;
    let tooltip;
    let brushOverlay;
    let rangeLineStart;
    let rangeLineEnd;
    let stickyLabel;

    // --- CHART SERIES ---

    let chart;
    let candleSeries, areaSeries, volumeSeries, ma30Series, ma90Series;
    // never reassign, only mutate (splice/push) so closure in subscribeCrosshairMove sees updates
    let comparisonSeries = [];
    // reactive copy updated only after series are ready to avoid render glitch
    let legendSeries = $state([]);
    let observer;
    let resizer;

    // --- INTERNAL STATE ---

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

    // --- TOUCH STATE ---
    let isTouchDragging = false;
    let touchStartX = 0;
    let touchStartRange = null;
    let lastPinchDist = 0;

    const PERIOD_DAYS = {
        '1w': 7, '1mo': 30, '3mo': 90,
        '6mo': 180, '1y': 365, '5y': 1825,
    };

    // --- FORMATTERS ---

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

        // comparison mode needs time-based range because series have different time points
        if (comparisonSeries.length > 0) {
            timeScale.setVisibleRange({ from: cutoffStr, to: processedData[processedData.length - 1].time });
        } else {
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

    // prevent scrolling past data boundaries (skipped in comparison mode where indices don't map 1:1)
    function clampRange(from, to) {
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

    // union of all unique dates across comparison series, sorted chronologically
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

    // convert pixel x-coordinate to a date string using the appropriate time axis
    function xToDate(x) {
        if (!chart) return null;
        const timeScale = chart.timeScale();
        const logical = timeScale.coordinateToLogical(x);
        if (logical === null) return null;
        const idx = Math.round(logical);

        if (comparisonSeries.length > 0 && mergedTimeAxis.length > 0) {
            if (idx < 0) return mergedTimeAxis[0];
            if (idx >= mergedTimeAxis.length) return mergedTimeAxis[mergedTimeAxis.length - 1];
            return mergedTimeAxis[idx];
        }

        if (idx < 0 || idx >= processedData.length) return null;
        return processedData[idx].time;
    }

    // --- ORANGE RANGE LINES ---

    // position the dashed orange boundary lines for the selected custom date range
    function updateRangeLines() {
        if (!rangeLineStart || !rangeLineEnd || !chart) return;
        if (!customRange || !customRange.start || !customRange.end) {
            rangeLineStart.style.display = 'none';
            rangeLineEnd.style.display = 'none';
            return;
        }

        const timeScale = chart.timeScale();
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

    // zoom centered on cursor position, min 3 bars visible
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
            // brush selection mode: start drawing range overlay
            isBrushing = true;
            brushStartX = x;
            brushStartTime = xToDate(x);
            if (brushOverlay) {
                brushOverlay.style.display = 'block';
                brushOverlay.style.left = `${x}px`;
                brushOverlay.style.width = '0px';
            }
        } else {
            // pan mode: grab and drag the visible range
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

            // finalize brush: zoom to selected range and notify parent
            if (brushStartTime && endTime && brushStartTime !== endTime) {
                const start = brushStartTime < endTime ? brushStartTime : endTime;
                const end = brushStartTime < endTime ? endTime : brushStartTime;

                if (comparisonSeries.length > 0) {
                    isProgrammaticRangeChange = true;
                    chart.timeScale().setVisibleRange({ from: start, to: end });
                    setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                } else {
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

    // --- TOUCH HANDLERS (mobile pan/pinch-zoom) ---

    function handleTouchStart(event) {
        if (!chart || processedData.length === 0) return;
        if (event.touches.length === 2) {
            // pinch-zoom: record initial distance
            const dx = event.touches[0].clientX - event.touches[1].clientX;
            const dy = event.touches[0].clientY - event.touches[1].clientY;
            lastPinchDist = Math.hypot(dx, dy);
            isTouchDragging = false;
            event.preventDefault();
        } else if (event.touches.length === 1) {
            // single-finger: pan
            const range = chart.timeScale().getVisibleLogicalRange();
            if (!range) return;
            isTouchDragging = true;
            touchStartX = event.touches[0].clientX;
            touchStartRange = { ...range };
        }
    }

    function handleTouchMove(event) {
        if (!chart) return;
        if (event.touches.length === 2) {
            // pinch-zoom
            event.preventDefault();
            const dx = event.touches[0].clientX - event.touches[1].clientX;
            const dy = event.touches[0].clientY - event.touches[1].clientY;
            const dist = Math.hypot(dx, dy);
            if (lastPinchDist === 0) { lastPinchDist = dist; return; }

            const timeScale = chart.timeScale();
            const range = timeScale.getVisibleLogicalRange();
            if (!range) return;

            const rangeSize = range.to - range.from;
            const scale = lastPinchDist / dist;
            const newSize = rangeSize * scale;
            if (newSize < 3) { lastPinchDist = dist; return; }

            const center = (range.from + range.to) / 2;
            const clamped = clampRange(center - newSize / 2, center + newSize / 2);
            timeScale.setVisibleLogicalRange(clamped);
            lastPinchDist = dist;
        } else if (event.touches.length === 1 && isTouchDragging && touchStartRange) {
            // single-finger pan
            const rect = chartContainer.getBoundingClientRect();
            const rangeSize = touchStartRange.to - touchStartRange.from;
            const pixelDelta = event.touches[0].clientX - touchStartX;
            const barDelta = -(pixelDelta / rect.width) * rangeSize;
            const clamped = clampRange(touchStartRange.from + barDelta, touchStartRange.to + barDelta);
            chart.timeScale().setVisibleLogicalRange(clamped);
        }
    }

    function handleTouchEnd() {
        isTouchDragging = false;
        touchStartRange = null;
        lastPinchDist = 0;
    }

    // --- PRICE LINE VISIBILITY ---

    // show sticky label pinned to top/bottom edge when the live price line scrolls off-screen
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

    // --- REACTIVE CURSOR + BRUSH ---

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

        // hide TradingView branding injected by lightweight-charts (debounced to prevent mutation loop)
        let _nukeTimer = null;
        const nuke = () => {
            chartContainer.querySelectorAll('div, a, span').forEach(el => {
                if (el.innerText?.includes('TradingView') || el.href?.includes('tradingview')) el.style.display = 'none';
            });
        };
        nuke();
        observer = new MutationObserver(() => {
            if (_nukeTimer) return;
            _nukeTimer = setTimeout(() => { _nukeTimer = null; nuke(); }, 200);
        });
        observer.observe(chartContainer, { childList: true, subtree: true });

        // --- STOCK MODE SERIES ---

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
                    // carry forward last known value for sparse series (different trading calendars)
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

                // sum volume across all comparison series for this date
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

                // render invisible first to measure actual height for positioning
                const tooltipWidth = 200;
                tooltip.style.visibility = 'hidden';
                tooltip.style.display = 'flex';

                tooltip.innerHTML = `
                    <div class="flex flex-col p-3 bg-[#16161e]/95 border border-white/10 rounded-xl shadow-2xl backdrop-blur-md min-w-[180px] gap-1.5 pointer-events-none">
                        <span class="text-[9px] text-white/30 uppercase font-black tracking-widest border-b border-white/5 pb-1 mb-1">${formatDate(param.time)}</span>
                        ${rows}
                        ${volTotal > 0 ? `<div class="flex justify-between items-center gap-4 pt-1 border-t border-white/5 mt-1"><span class="text-[9px] text-purple-400/60 uppercase font-bold">Vol</span><span class="text-xs text-purple-400 font-bold">${formatVolume(volTotal)}</span></div>` : ''}
                    </div>`;
                // clamp tooltip within chart bounds
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

        // --- EVENT LISTENERS ---

        chartContainer.addEventListener('wheel', handleWheel, { passive: false });
        chartContainer.addEventListener('mousedown', handleMouseDown);
        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        chartContainer.addEventListener('touchstart', handleTouchStart, { passive: false });
        chartContainer.addEventListener('touchmove', handleTouchMove, { passive: false });
        chartContainer.addEventListener('touchend', handleTouchEnd);

        // double-click: snap to custom range if set, otherwise reset period
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

        resizer = new ResizeObserver(() => {
            if (!chartContainer) return;
            chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
            if (currentVisibleRange) chart.timeScale().setVisibleLogicalRange(currentVisibleRange);
            updatePriceLineVisibility();
            updateRangeLines();
        });
        resizer.observe(chartContainer);
    });

    // --- REACTIVE: STOCK DATA ---

    $effect(() => {
        // use spread to get a plain array — avoids deep proxy clone overhead from $state.snapshot
        const raw = [...data];
        if (!candleSeries) return;

        if (raw.length === 0) {
            // clear chart when data is reset (stock switch) so stale data doesn't show
            try {
                candleSeries.setData([]);
                areaSeries.setData([]);
                ma30Series.setData([]);
                ma90Series.setData([]);
                volumeSeries.setData([]);
                if (activePriceLine) { try { candleSeries.removePriceLine(activePriceLine); } catch(e) {} activePriceLine = null; }
            } catch(e) { console.error('Chart clear error:', e); }
            processedData = [];
            lastDataKey = '';
            return;
        }

        try {
            // deduplicate and sort by date — filter out rows with missing time/close
            const uniqueMap = new Map();
            for (const item of raw) {
                if (item.time && item.close != null && item.close !== 0) {
                    uniqueMap.set(item.time, item);
                }
            }
            if (uniqueMap.size === 0) return;
            processedData = Array.from(uniqueMap.values()).sort((a, b) => {
                if (a.time < b.time) return -1;
                if (a.time > b.time) return 1;
                return 0;
            });

            candleSeries.setData(processedData.map(d => ({
                time: d.time, open: d.open || d.close, high: d.high || d.close,
                low: d.low || d.close, close: d.close,
            })));
            areaSeries.setData(processedData.map(d => ({ time: d.time, value: d.close })));
            ma30Series.setData(processedData.map(d => ({ time: d.time, value: d.ma30 || 0 })));
            ma90Series.setData(processedData.map(d => ({ time: d.time, value: d.ma90 || 0 })));

            // highlight volume bars above 80% of max — use loop instead of Math.max(...spread)
            let maxVol = 0;
            for (const d of processedData) { if (d.volume > maxVol) maxVol = d.volume; }
            volumeSeries.setData(processedData.map(d => ({
                time: d.time, value: d.volume || 0,
                color: d.volume > (maxVol * 0.8) ? '#a855f7' : 'rgba(168, 85, 247, 0.3)',
            })));

            // update live price dashed line
            if (activePriceLine) { try { candleSeries.removePriceLine(activePriceLine); } catch(e) {} }
            lastClosePrice = processedData[processedData.length - 1].close;
            activePriceLine = candleSeries.createPriceLine({
                price: lastClosePrice, color: '#ffffff', lineWidth: 1,
                lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '',
            });

            // apply period/range only when the underlying dataset changes (new symbol or new data)
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
        } catch(e) {
            console.error('Chart data processing error:', e);
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

    // fallback color/name maps for major indices
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

    // aggregate volume across all comparison series per date
    function setComparisonVolume(cData) {
        if (hideVolume) {
            volumeSeries.setData([]);
            return;
        }
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

    // first-time full render: clear stock series, create all comparison line series
    function renderComparisonFull(cData) {
        if (!chart) return;

        // hide stock-mode series (volumeSeries reused for aggregated volume)
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

        comparisonSeries.forEach(cs => {
            try { chart.removeSeries(cs.series); } catch(e) {}
        });
        comparisonSeries.splice(0);

        // create a % return line for each index
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

            // axis label showing final % return
            const lastPct = s.points[s.points.length - 1]?.pct ?? 0;
            lineSeries.createPriceLine({
                price: lastPct, color: color, lineWidth: 0,
                lineStyle: LineStyle.Solid, axisLabelVisible: true,
                title: '', lineVisible: false,
            });

            comparisonSeries.push({ symbol: s.symbol, series: lineSeries, color, name });
        });

        // dashed zero baseline
        if (comparisonSeries.length > 0) {
            try {
                comparisonSeries[0].series.createPriceLine({
                    price: 0, color: 'rgba(255,255,255,0.15)', lineWidth: 1,
                    lineStyle: LineStyle.Dashed, axisLabelVisible: false,
                });
            } catch(e) {}
        }

        buildMergedTimeAxis(cData);
        processedData = mergedTimeAxis.map(t => ({ time: t, close: 0 }));

        setComparisonVolume(cData);

        if (currentPeriod) {
            applyPeriodRange(currentPeriod);
        } else if (customRange && customRange.start && customRange.end) {
            chart.timeScale().setVisibleRange({ from: customRange.start, to: customRange.end });
        } else {
            applyPeriodRange('1y');
        }

        legendSeries = comparisonSeries.map(cs => ({
            symbol: cs.symbol,
            color: COMPARE_COLORS[cs.symbol] || '#8b5cf6',
            name: COMPARE_NAMES[cs.symbol] || cs.symbol
        }));
    }

    // incremental update: atomic rebuild of all series to keep visible range stable
    function updateComparisonSeries(cData) {
        if (!chart) return;

        // save visible time range before modifications
        let savedFrom = null, savedTo = null;
        try {
            const vr = chart.timeScale().getVisibleRange();
            if (vr) { savedFrom = vr.from; savedTo = vr.to; }
        } catch(e) {}

        // disable edge constraints during rebuild to prevent auto-clamping
        chart.timeScale().applyOptions({ fixLeftEdge: false, fixRightEdge: false });

        // full atomic rebuild avoids intermediate states that shift the time axis
        comparisonSeries.forEach(cs => {
            try { chart.removeSeries(cs.series); } catch(e) {}
        });
        comparisonSeries.splice(0);

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

        if (comparisonSeries.length > 0) {
            try {
                comparisonSeries[0].series.createPriceLine({
                    price: 0, color: 'rgba(255,255,255,0.15)', lineWidth: 1,
                    lineStyle: LineStyle.Dashed, axisLabelVisible: false,
                });
            } catch(e) {}
        }

        buildMergedTimeAxis(cData);
        processedData = mergedTimeAxis.map(t => ({ time: t, close: 0 }));

        setComparisonVolume(cData);

        legendSeries = comparisonSeries.map(cs => ({
            symbol: cs.symbol,
            color: COMPARE_COLORS[cs.symbol] || '#8b5cf6',
            name: COMPARE_NAMES[cs.symbol] || cs.symbol
        }));

        // restore visible range via bar-index mapping (time strings snap unpredictably when bars change)
        if (savedFrom && savedTo && mergedTimeAxis.length > 0) {
            isProgrammaticRangeChange = true;
            let fromIdx = 0;
            for (let i = 0; i < mergedTimeAxis.length; i++) {
                if (mergedTimeAxis[i] >= savedFrom) { fromIdx = i; break; }
            }
            let toIdx = mergedTimeAxis.length - 1;
            for (let i = mergedTimeAxis.length - 1; i >= 0; i--) {
                if (mergedTimeAxis[i] <= savedTo) { toIdx = i; break; }
            }
            chart.timeScale().setVisibleLogicalRange({ from: fromIdx, to: toIdx });
        }
        chart.timeScale().applyOptions({ fixLeftEdge: true, fixRightEdge: true });
        if (savedFrom && savedTo) {
            setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
        }
    }

    let _lastCompareDataVersion = 0;

    // react to compareData changes: determine whether to do a full render, incremental update, or in-place data swap
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
            // same symbols: update series data in-place if version changed
            if (dataVersion === _lastCompareDataVersion && dataVersion > 0) return;
            _lastCompareDataVersion = dataVersion;
            _lastCompareSymbols = newSymbols;

            // check if time axis changed (symbols are the same, so usually only values differ)
            const firstPoints = cData.series[0]?.points;
            const axisChanged = !firstPoints || mergedTimeAxis.length === 0
                || firstPoints.length !== mergedTimeAxis.length
                || firstPoints[0]?.time !== mergedTimeAxis[0]
                || firstPoints[firstPoints.length - 1]?.time !== mergedTimeAxis[mergedTimeAxis.length - 1];

            if (axisChanged) {
                // time axis changed: full range save/restore needed
                let savedFrom = null, savedTo = null;
                try {
                    const vr = chart.timeScale().getVisibleRange();
                    if (vr) { savedFrom = vr.from; savedTo = vr.to; }
                } catch(e) {}

                chart.timeScale().applyOptions({ fixLeftEdge: false, fixRightEdge: false });

                comparisonSeries.forEach(cs => {
                    const s = cData.series.find(s => s.symbol === cs.symbol);
                    if (s) cs.series.setData(s.points.map(p => ({ time: p.time, value: p.pct })));
                });
                buildMergedTimeAxis(cData);
                processedData = mergedTimeAxis.map(t => ({ time: t, close: 0 }));

                if (savedFrom && savedTo && mergedTimeAxis.length > 0) {
                    isProgrammaticRangeChange = true;
                    let fromIdx = 0;
                    for (let i = 0; i < mergedTimeAxis.length; i++) {
                        if (mergedTimeAxis[i] >= savedFrom) { fromIdx = i; break; }
                    }
                    let toIdx = mergedTimeAxis.length - 1;
                    for (let i = mergedTimeAxis.length - 1; i >= 0; i--) {
                        if (mergedTimeAxis[i] <= savedTo) { toIdx = i; break; }
                    }
                    chart.timeScale().setVisibleLogicalRange({ from: fromIdx, to: toIdx });
                }
                chart.timeScale().applyOptions({ fixLeftEdge: true, fixRightEdge: true });
                if (savedFrom && savedTo) {
                    setTimeout(() => { isProgrammaticRangeChange = false; }, 50);
                }
            } else {
                // same time axis: fast value-only update, skip range save/restore
                for (const cs of comparisonSeries) {
                    const s = cData.series.find(s => s.symbol === cs.symbol);
                    if (!s) continue;
                    const pts = s.points;
                    const mapped = new Array(pts.length);
                    for (let i = 0; i < pts.length; i++) {
                        mapped[i] = { time: pts[i].time, value: pts[i].pct };
                    }
                    cs.series.setData(mapped);
                }
            }
            return;
        }

        // detect whether this is a full mode switch or incremental index add/remove
        const isFirstLoad = _lastCompareSymbols === '';
        const prevSymbolSet = new Set(_lastCompareSymbols.split(',').filter(Boolean));
        const nextSymbolSet = new Set(newSymbols.split(',').filter(Boolean));
        const isCompleteChange = isFirstLoad || [...prevSymbolSet].every(s => !nextSymbolSet.has(s));
        _lastCompareSymbols = newSymbols;
        _lastCompareDataVersion = dataVersion;

        legendSeries = [];
        if (isCompleteChange) {
            await tick();
            renderComparisonFull(cData);
        } else {
            updateComparisonSeries(cData);
        }
    });

    // --- CLEANUP ---

    onDestroy(() => {
        chartContainer?.removeEventListener('wheel', handleWheel);
        chartContainer?.removeEventListener('mousedown', handleMouseDown);
        window?.removeEventListener('mousemove', handleMouseMove);
        window?.removeEventListener('mouseup', handleMouseUp);
        chartContainer?.removeEventListener('touchstart', handleTouchStart);
        chartContainer?.removeEventListener('touchmove', handleTouchMove);
        chartContainer?.removeEventListener('touchend', handleTouchEnd);
        resizer?.disconnect();
        observer?.disconnect();
        chart?.remove();
    });

    // --- SECTOR ABBREVIATIONS (for legend labels) ---

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

<div bind:this={chartContainer} class="w-full h-full relative rounded-2xl overflow-hidden bg-[#0d0d12]" style="min-height:clamp(220px,35vh,600px);transition:none !important;cursor:crosshair;touch-action:none">

    {#if compareData && compareData.series && compareData.series.length > 0}
        <div class="absolute top-4 left-6 max-sm:top-2 max-sm:left-2 z-[110] flex gap-4 max-sm:gap-2 pointer-events-none p-2 max-sm:p-1.5 bg-black/20 backdrop-blur-sm rounded-lg flex-wrap">
            {#each legendSeries as s}
                <div class="flex items-center gap-1.5">
                    <div class="w-4 h-[2px] rounded-full" style="background: {s.color}"></div>
                    <span class="text-[9px] uppercase font-black tracking-widest" style="color: {s.color}">{s.name}</span>
                </div>
            {/each}
        </div>
    {:else}
        <div class="absolute top-4 left-6 max-sm:top-2 max-sm:left-2 z-[110] flex gap-5 max-sm:gap-2 pointer-events-none p-2 max-sm:p-1.5 bg-black/20 backdrop-blur-sm rounded-lg flex-wrap">
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