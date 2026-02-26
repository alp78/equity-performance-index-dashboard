<!--
  ═══════════════════════════════════════════════════════════════════════════
   Chart — Financial Candlestick + Comparison Line Chart (1,200 lines)
  ═══════════════════════════════════════════════════════════════════════════
   Primary chart component using lightweight-charts (lazy-loaded).
   Two rendering modes:
     • Stock mode  — OHLC candlesticks, MA30/MA90 lines, volume histogram
     • Comparison  — rebased % return lines with zero baseline and dual
                     time-axis handling for cross-calendar alignment

   Interaction:
     • Mouse wheel zoom, drag pan, touch pinch-zoom
     • Brush selection (shift+drag) for custom date ranges
     • Double-click to reset visible range
     • Context-aware tooltip (OHLC vs comparison format)
     • Sticky price label for off-screen live price

   Props: data, comparisonData, currentPeriod, customRange, onBrushSelect
   Placement : main content area — all three dashboard modes
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { onMount, onDestroy, tick } from 'svelte';
    import { SECTOR_ABBREV } from '$lib/theme.js';
    import { INDEX_META_BY_TICKER } from '$lib/index-registry.js';
    // lightweight-charts is loaded lazily in onMount to keep it out of the main bundle
    let createChart, ColorType, CandlestickSeries, AreaSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle;

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
        highlightSymbol = null,
        hideLegend = false,
        currencyLabel = '',
    } = $props();

    // --- DOM REFS ---

    let chartContainer;
    let tooltip;
    let brushOverlay;
    let rangeLineStart;
    let rangeLineEnd;
    let stickyLabel;

    // --- CHART SERIES ---

    let chart = $state(null);
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
    let _rawComparisonData = null; // store raw points for period rebasing

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

    /**
     * Rebase comparison series so the first visible point = 0%.
     * Uses raw close prices: new_pct = ((close - period_base) / period_base) * 100
     * @param {string} period - period key like '1y', '3mo', 'max'
     * @param {string} [startDate] - explicit start date (for custom range)
     */
    function rebaseComparison(period, startDate) {
        if (!_rawComparisonData || comparisonSeries.length === 0) return;

        let cutoffStr = startDate || null;
        if (!cutoffStr && period && period !== 'max') {
            const days = PERIOD_DAYS[period];
            if (days && processedData.length > 0) {
                const lastDate = new Date(processedData[processedData.length - 1].time);
                const cutoff = new Date(lastDate);
                cutoff.setDate(cutoff.getDate() - days);
                cutoffStr = cutoff.toISOString().split('T')[0];
            }
        }

        for (const cs of comparisonSeries) {
            const raw = _rawComparisonData[cs.symbol];
            if (!raw || raw.length === 0) continue;

            // find period base close: first point at or after cutoff
            let baseClose;
            if (cutoffStr) {
                const startPt = raw.find(p => p.time >= cutoffStr);
                baseClose = startPt ? startPt.close : raw[0].close;
            } else {
                baseClose = raw[0].close;
            }

            if (!baseClose || baseClose === 0) continue;

            const rebased = raw.map(p => ({
                time: p.time,
                value: ((p.close - baseClose) / baseClose) * 100,
            }));
            cs.series.setData(rebased);

            // update axis label to final rebased pct
            // remove old price lines and add new one
            try {
                const lines = cs.series.priceLines?.() || [];
                lines.forEach(l => { try { cs.series.removePriceLine(l); } catch(e) {} });
            } catch(e) {}
            const lastPct = rebased[rebased.length - 1]?.value ?? 0;
            cs.series.createPriceLine({
                price: lastPct, color: dimColor(cs.color), lineWidth: 0,
                lineStyle: LineStyle.Solid, axisLabelVisible: true,
                title: '', lineVisible: false,
            });
        }

        // re-add zero baseline on first series
        if (comparisonSeries.length > 0) {
            try {
                comparisonSeries[0].series.createPriceLine({
                    price: 0, color: 'rgba(255,255,255,0.08)', lineWidth: 0.5,
                    lineStyle: LineStyle.Dashed, axisLabelVisible: false,
                });
            } catch(e) {}
        }
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

    // Darken a hex color by mixing toward black for muted axis labels
    function dimColor(hex, mix = 0.55) {
        const r = Math.round(parseInt(hex.slice(1, 3), 16) * (1 - mix));
        const g = Math.round(parseInt(hex.slice(3, 5), 16) * (1 - mix));
        const b = Math.round(parseInt(hex.slice(5, 7), 16) * (1 - mix));
        return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${b.toString(16).padStart(2,'0')}`;
    }

    // --- THEME-AWARE CHART COLORS ---

    function isDarkTheme() {
        return document.documentElement.getAttribute('data-theme') !== 'light';
    }

    function chartThemeColors(dark) {
        return dark ? {
            textColor: 'rgba(255, 255, 255, 0.6)',
            gridColor: 'rgba(255, 255, 255, 0.04)',
            crossColor: 'rgba(255, 255, 255, 0.15)',
        } : {
            textColor: 'rgba(15, 23, 42, 0.5)',
            gridColor: 'rgba(15, 23, 42, 0.06)',
            crossColor: 'rgba(15, 23, 42, 0.12)',
        };
    }

    let themeObserver;

    // --- CHART INIT ---

    onMount(async () => {
        const lc = await import('lightweight-charts');
        createChart = lc.createChart;
        ColorType = lc.ColorType;
        CandlestickSeries = lc.CandlestickSeries;
        AreaSeries = lc.AreaSeries;
        HistogramSeries = lc.HistogramSeries;
        LineSeries = lc.LineSeries;
        CrosshairMode = lc.CrosshairMode;
        LineStyle = lc.LineStyle;

        const tc = chartThemeColors(isDarkTheme());

        chart = createChart(chartContainer, {
            layout: {
                background: { type: ColorType.Solid, color: 'transparent' },
                textColor: tc.textColor,
                fontSize: 11,
                fontFamily: 'Geist, Inter, system-ui, sans-serif',
            },
            grid: {
                vertLines: { visible: false },
                horzLines: { color: tc.gridColor },
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
                vertLine: { color: tc.crossColor, style: LineStyle.SparseDotted, labelVisible: false },
                horzLine: { color: tc.crossColor, style: LineStyle.SparseDotted, labelVisible: true },
            },
            handleScroll: false,
            handleScale: false,
        });

        // Watch for theme changes and update chart colors
        themeObserver = new MutationObserver(() => {
            if (!chart) return;
            const c = chartThemeColors(isDarkTheme());
            chart.applyOptions({
                layout: { textColor: c.textColor },
                grid: { horzLines: { color: c.gridColor } },
                crosshair: {
                    vertLine: { color: c.crossColor },
                    horzLine: { color: c.crossColor },
                },
            });
        });
        themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

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
            upColor: '#26A69A',
            downColor: '#EF5350',
            borderUpColor: '#26A69A',
            borderDownColor: '#EF5350',
            wickUpColor: 'rgba(38, 166, 154, 0.6)',
            wickDownColor: 'rgba(239, 83, 80, 0.6)',
            lastValueVisible: false,
            priceLineVisible: false,
        });

        areaSeries = chart.addSeries(AreaSeries, {
            lineColor: 'rgba(59, 130, 246, 0.5)',
            topColor: 'rgba(59, 130, 246, 0.08)',
            bottomColor: 'rgba(59, 130, 246, 0)',
            lineWidth: 0.5,
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
        });

        ma30Series = chart.addSeries(LineSeries, {
            color: '#3B82F6', lineWidth: 0.5, lineStyle: LineStyle.Dashed,
            lastValueVisible: false, priceLineVisible: false,
        });
        ma90Series = chart.addSeries(LineSeries, {
            color: '#A855F7', lineWidth: 0.5, lineStyle: LineStyle.Dashed,
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
                let hasAnyData = false;

                // Pass 1: collect all pct values for relative ranking
                const entries = [];
                for (const cs of comparisonSeries) {
                    const sData = param.seriesData?.get(cs.series);
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
                    let rawClose = '';
                    if (compareData && compareData.series) {
                        const s = compareData.series.find(s => s.symbol === cs.symbol);
                        if (s) {
                            const pt = s.points.find(p => p.time === param.time);
                            if (pt && pt.close != null) rawClose = pt.close.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1});
                        }
                    }
                    entries.push({ pct, color, name, rawClose, symbol: cs.symbol });
                }

                // Pass 2: rank by pct and assign relative color
                // Best performer = green, worst = red, middle = interpolated
                const sorted = [...entries].sort((a, b) => b.pct - a.pct);
                const rankMap = {};
                sorted.forEach((e, i) => { rankMap[e.symbol] = i; });
                const n = sorted.length;

                function relativeColor(symbol) {
                    if (n <= 1) return '#FAFAFA';
                    const rank = rankMap[symbol]; // 0 = best, n-1 = worst
                    const t = rank / (n - 1);     // 0 = best → 1 = worst
                    // sage (#5B9A6F) → neutral (#A1A1AA) → rose (#B85C5C)
                    if (t <= 0.5) {
                        const u = t * 2; // 0→1 within top half
                        const r = Math.round(91 + u * (161 - 91));
                        const g = Math.round(154 + u * (161 - 154));
                        const b = Math.round(111 + u * (170 - 111));
                        return `rgb(${r},${g},${b})`;
                    } else {
                        const u = (t - 0.5) * 2; // 0→1 within bottom half
                        const r = Math.round(161 + u * (184 - 161));
                        const g = Math.round(161 + u * (92 - 161));
                        const b = Math.round(170 + u * (92 - 170));
                        return `rgb(${r},${g},${b})`;
                    }
                }

                // Pass 3: build rows sorted by rank (best performer first)
                let rows = '';
                for (const e of sorted) {
                    const sign = e.pct >= 0 ? '+' : '';
                    const pctColor = relativeColor(e.symbol);
                    rows += `
                        <div class="flex items-center gap-2">
                            <div style="width:8px;height:3px;border-radius:2px;background:${e.color};flex-shrink:0"></div>
                            <span class="text-[11px] uppercase font-bold" style="color:${e.color};min-width:55px">${e.name}</span>
                            <span class="text-[13px] font-semibold tabular-nums ml-auto" style="color:${pctColor}">${sign}${e.pct.toFixed(2)}%</span>
                        </div>
                        ${e.rawClose ? `<div class="flex justify-end"><span class="text-[11px] tt-muted tabular-nums">${currencyLabel}${e.rawClose}</span></div>` : ''}
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
                    <div class="flex flex-col p-3 tt-bg border tt-border rounded-lg shadow-lg shadow-black/40 min-w-[180px] gap-1.5 pointer-events-none">
                        <span class="text-[11px] tt-muted uppercase font-semibold tracking-widest border-b tt-border pb-1 mb-1">${formatDate(param.time)}</span>
                        ${rows}
                        ${volTotal > 0 ? `<div class="flex justify-between items-center gap-4 pt-1 border-t tt-border mt-1"><span class="text-[11px] tt-muted uppercase font-bold">Vol</span><span class="text-xs tt-text font-bold">${formatVolume(volTotal)}</span></div>` : ''}
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
                const colorClass = diff >= 0 ? 'tt-positive' : 'tt-negative';
                const candleUp = closePrice >= openPrice;

                tooltip.style.display = 'flex';
                const tooltipWidth = 190, tooltipHeight = 270;
                let left = param.point.x + 20, top = param.point.y + 20;
                if (left + tooltipWidth > chartContainer.clientWidth) left = param.point.x - tooltipWidth - 20;
                if (top + tooltipHeight > chartContainer.clientHeight) top = param.point.y - tooltipHeight - 20;
                tooltip.style.left = `${left}px`;
                tooltip.style.top = `${top}px`;

                tooltip.innerHTML = `
                    <div class="flex flex-col p-3 tt-bg border tt-border rounded-lg shadow-lg shadow-black/40 min-w-[170px] gap-1.5 pointer-events-none" style="font-variant-numeric:tabular-nums;">
                        <span class="text-[11px] tt-muted uppercase font-semibold tracking-widest border-b tt-border pb-1 mb-1">${formatDate(param.time)}</span>

                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[11px] tt-muted uppercase font-bold">Open</span>
                            <span class="text-xs tt-text font-semibold">${c}${openPrice.toFixed(2)}</span>
                        </div>

                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[11px] tt-muted uppercase font-bold">High</span>
                            <span class="text-xs tt-text font-semibold">${c}${highPrice.toFixed(2)}</span>
                        </div>

                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[11px] tt-muted uppercase font-bold">Low</span>
                            <span class="text-xs tt-text font-semibold">${c}${lowPrice.toFixed(2)}</span>
                        </div>

                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[11px] tt-text uppercase font-bold">Close</span>
                            <span class="text-xs font-semibold ${candleUp ? 'tt-positive' : 'tt-negative'}">${c}${closePrice.toFixed(2)}</span>
                        </div>

                        <div class="flex justify-between items-center gap-4 border-t tt-border pt-1 mt-1">
                            <span class="text-[11px] tt-muted uppercase font-bold">vs Live</span>
                            <span class="text-[12px] font-semibold ${colorClass}">${diff >= 0 ? '+' : ''}${diff.toFixed(2)} (${diffPct.toFixed(2)}%)</span>
                        </div>

                        <div class="flex justify-between items-center gap-4 border-t tt-border pt-1 mt-1">
                            <span class="text-[11px] uppercase font-bold" style="color:#3B82F6">MA 30</span>
                            <span class="text-xs font-bold" style="color:#3B82F6">${m30Data ? c + m30Data.value.toFixed(2) : '—'}</span>
                        </div>

                        <div class="flex justify-between items-center gap-4">
                            <span class="text-[11px] uppercase font-bold" style="color:#A855F7">MA 90</span>
                            <span class="text-xs font-bold" style="color:#A855F7">${m90Data ? c + m90Data.value.toFixed(2) : '—'}</span>
                        </div>

                        <div class="flex justify-between items-center gap-4 pt-1 border-t tt-border">
                            <span class="text-[11px] uppercase font-bold" style="color:#64748B">Vol</span>
                            <span class="text-xs font-bold" style="color:#64748B">${vData ? formatVolume(vData.value) : '—'}</span>
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
                    rebaseComparison(null, customRange.start);
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
                color: d.volume > (maxVol * 0.8) ? '#64748B' : 'rgba(100, 116, 139, 0.15)',
            })));

            // update live price dashed line
            if (activePriceLine) { try { candleSeries.removePriceLine(activePriceLine); } catch(e) {} }
            lastClosePrice = processedData[processedData.length - 1].close;
            activePriceLine = candleSeries.createPriceLine({
                price: lastClosePrice, color: '#717171', lineWidth: 0.5,
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
            if (comparisonSeries.length > 0) {
                rebaseComparison(period);
            }
            applyPeriodRange(period);
            setTimeout(() => updatePriceLineVisibility(), 100);
        }
    });

    // --- REACTIVE: COMPARISON DATA ---

    // fallback color/name maps built from centralized index registry
    const _DEFAULT_COLORS = Object.fromEntries(
        Object.entries(INDEX_META_BY_TICKER).map(([ticker, m]) => [ticker, m.color])
    );
    const _DEFAULT_NAMES = Object.fromEntries(
        Object.entries(INDEX_META_BY_TICKER).map(([ticker, m]) => [ticker, m.name])
    );
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
            color: vol > (maxVol * 0.8) ? '#64748B' : 'rgba(100, 116, 139, 0.15)',
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

        // store raw close data for period rebasing
        _rawComparisonData = {};
        for (const s of cData.series) {
            _rawComparisonData[s.symbol] = s.points.map(p => ({ time: p.time, close: p.close ?? (100 + (p.pct || 0)), pct: p.pct }));
        }

        // create a % return line for each index
        cData.series.forEach((s) => {
            const color = COMPARE_COLORS[s.symbol] || '#8b5cf6';
            const name = COMPARE_NAMES[s.symbol] || s.symbol;
            const lineSeries = chart.addSeries(LineSeries, {
                color: color,
                lineWidth: 0.5,
                title: '',
                lastValueVisible: false,
                priceLineVisible: false,
                priceFormat: { type: 'custom', formatter: (p) => p.toFixed(1) + '%' },
                crosshairMarkerVisible: true,
                crosshairMarkerRadius: 2,
            });
            lineSeries.setData(s.points.map(p => ({ time: p.time, value: p.pct })));

            // axis label showing final % return
            const lastPct = s.points[s.points.length - 1]?.pct ?? 0;
            lineSeries.createPriceLine({
                price: lastPct, color: dimColor(color), lineWidth: 0,
                lineStyle: LineStyle.Solid, axisLabelVisible: true,
                title: '', lineVisible: false,
            });

            comparisonSeries.push({ symbol: s.symbol, series: lineSeries, color, name });
        });

        // dashed zero baseline
        if (comparisonSeries.length > 0) {
            try {
                comparisonSeries[0].series.createPriceLine({
                    price: 0, color: 'rgba(255,255,255,0.08)', lineWidth: 0.5,
                    lineStyle: LineStyle.Dashed, axisLabelVisible: false,
                });
            } catch(e) {}
        }

        buildMergedTimeAxis(cData);
        processedData = mergedTimeAxis.map(t => ({ time: t, close: 0 }));

        setComparisonVolume(cData);

        if (customRange && customRange.start && customRange.end) {
            rebaseComparison(null, customRange.start);
            applyPeriodRange('max');
            chart.timeScale().setVisibleRange({ from: customRange.start, to: customRange.end });
        } else {
            const effectivePeriod = currentPeriod || '1y';
            rebaseComparison(effectivePeriod);
            applyPeriodRange(effectivePeriod);
        }

        legendSeries = comparisonSeries.map(cs => ({
            symbol: cs.symbol,
            color: COMPARE_COLORS[cs.symbol] || '#8b5cf6',
            name: COMPARE_NAMES[cs.symbol] || cs.symbol
        }));

        // apply highlight if one is active
        applyHighlight(highlightSymbol);
    }

    // Dim/brighten comparison lines based on highlightSymbol
    // Subtle emphasis: slightly dim non-highlighted lines, slightly thicken highlighted
    function applyHighlight(hl) {
        if (!comparisonSeries.length) return;
        for (const cs of comparisonSeries) {
            if (hl && hl !== cs.symbol) {
                cs.series.applyOptions({
                    color: dimColor(cs.color, 0.35),
                    lineWidth: 0.5,
                });
            } else if (hl && hl === cs.symbol) {
                cs.series.applyOptions({ color: cs.color, lineWidth: 1.5 });
            } else {
                cs.series.applyOptions({ color: cs.color, lineWidth: 0.5 });
            }
        }
    }

    $effect(() => { applyHighlight(highlightSymbol); });

    // incremental update: atomic rebuild of all series to keep visible range stable
    function updateComparisonSeries(cData) {
        if (!chart) return;

        // update raw data for rebasing
        _rawComparisonData = {};
        for (const s of cData.series) {
            _rawComparisonData[s.symbol] = s.points.map(p => ({ time: p.time, close: p.close ?? (100 + (p.pct || 0)), pct: p.pct }));
        }

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
                lineWidth: 0.5,
                title: '',
                lastValueVisible: false,
                priceLineVisible: false,
                priceFormat: { type: 'custom', formatter: (p) => p.toFixed(1) + '%' },
                crosshairMarkerVisible: true,
                crosshairMarkerRadius: 2,
            });
            lineSeries.setData(s.points.map(p => ({ time: p.time, value: p.pct })));

            const lastPct = s.points[s.points.length - 1]?.pct ?? 0;
            lineSeries.createPriceLine({
                price: lastPct, color: dimColor(color), lineWidth: 0,
                lineStyle: LineStyle.Solid, axisLabelVisible: true,
                title: '', lineVisible: false,
            });

            comparisonSeries.push({ symbol: s.symbol, series: lineSeries, color, name });
        });

        if (comparisonSeries.length > 0) {
            try {
                comparisonSeries[0].series.createPriceLine({
                    price: 0, color: 'rgba(255,255,255,0.08)', lineWidth: 0.5,
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

        // rebase to current period
        if (customRange && customRange.start && customRange.end) {
            rebaseComparison(null, customRange.start);
        } else {
            rebaseComparison(currentPeriod || '1y');
        }

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
        const c = chart; // track chart readiness so this effect re-runs after async init
        if (!cData || !cData.series || cData.series.length === 0) {
            if (c) {
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

        if (!c) return; // chart not yet initialized (async import pending)

        const newSymbols = cData.series.map(s => s.symbol).sort().join(',');
        const dataVersion = cData._version || 0;

        if (newSymbols === _lastCompareSymbols && comparisonSeries.length > 0) {
            // same symbols: update series data in-place if version changed
            if (dataVersion === _lastCompareDataVersion && dataVersion > 0) return;
            _lastCompareDataVersion = dataVersion;
            _lastCompareSymbols = newSymbols;

            // update raw data for rebasing (sector switch with same index symbols)
            _rawComparisonData = {};
            for (const s of cData.series) {
                _rawComparisonData[s.symbol] = s.points.map(p => ({
                    time: p.time,
                    close: p.close ?? (100 + (p.pct || 0)),
                    pct: p.pct,
                }));
            }

            // check if time axis changed (symbols are the same, so usually only values differ)
            const firstPoints = cData.series[0]?.points;
            const axisChanged = !firstPoints || mergedTimeAxis.length === 0
                || firstPoints.length !== mergedTimeAxis.length
                || firstPoints[0]?.time !== mergedTimeAxis[0]
                || firstPoints[firstPoints.length - 1]?.time !== mergedTimeAxis[mergedTimeAxis.length - 1];

            if (axisChanged) {
                // time axis changed: full range save/restore needed
                chart.timeScale().applyOptions({ fixLeftEdge: false, fixRightEdge: false });

                comparisonSeries.forEach(cs => {
                    const s = cData.series.find(s => s.symbol === cs.symbol);
                    if (s) cs.series.setData(s.points.map(p => ({ time: p.time, value: p.pct })));
                });
                buildMergedTimeAxis(cData);
                processedData = mergedTimeAxis.map(t => ({ time: t, close: 0 }));

                chart.timeScale().applyOptions({ fixLeftEdge: true, fixRightEdge: true });
            } else {
                // same time axis: fast value-only update
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

            // rebase to current period and apply range (same as renderComparisonFull)
            if (customRange && customRange.start && customRange.end) {
                rebaseComparison(null, customRange.start);
                applyPeriodRange('max');
                chart.timeScale().setVisibleRange({ from: customRange.start, to: customRange.end });
            } else {
                const effectivePeriod = currentPeriod || '1y';
                rebaseComparison(effectivePeriod);
                applyPeriodRange(effectivePeriod);
            }

            applyHighlight(highlightSymbol);
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
        themeObserver?.disconnect();
        chart?.remove();
    });

    function abbrevSector(name) { return SECTOR_ABBREV[name] || name; }
</script>

<div bind:this={chartContainer} role="img" aria-label="Financial chart" class="w-full h-full relative rounded-xl overflow-hidden bg-surface-0" style="min-height:clamp(220px,35vh,600px);transition:none !important;cursor:crosshair;touch-action:none">

    {#if compareData && compareData.series && compareData.series.length > 0}
        {#if !hideLegend}
            <div class="absolute top-4 left-6 max-sm:top-2 max-sm:left-2 z-[110] flex gap-4 max-sm:gap-2 pointer-events-none p-2 max-sm:p-1.5 bg-surface-0/80 rounded-lg flex-wrap">
                {#each legendSeries as s}
                    <div class="flex items-center gap-1.5">
                        <div class="w-4 h-[2px] rounded-full" style="background: {s.color}" aria-hidden="true"></div>
                        <span class="text-[11px] uppercase font-semibold tracking-widest" style="color: {s.color}">{s.name}</span>
                    </div>
                {/each}
            </div>
        {/if}
    {:else}
        <div class="absolute top-4 left-6 max-sm:top-2 max-sm:left-2 z-[110] flex gap-5 max-sm:gap-2 pointer-events-none p-2 max-sm:p-1.5 bg-surface-0/80 rounded-lg flex-wrap">
            <div class="flex items-center gap-1.5"><div class="w-2 h-3 bg-up/60 rounded-[1px]" aria-hidden="true"></div><div class="w-2 h-3 bg-down/60 rounded-[1px]" aria-hidden="true"></div><span class="text-[11px] text-text-muted uppercase font-semibold tracking-widest ml-1">OHLC</span></div>
            <div class="flex items-center gap-2"><div class="w-4 h-0 border-t-2 border-dashed border-text" aria-hidden="true"></div><span class="text-[11px] text-text font-semibold tracking-widest">Live</span></div>
            <div class="flex items-center gap-2"><div class="w-4 h-0 border-t border-dashed" style="border-color:#3B82F6" aria-hidden="true"></div><span class="text-[11px] uppercase font-semibold tracking-widest" style="color:#3B82F6">MA 30</span></div>
            <div class="flex items-center gap-2"><div class="w-4 h-0 border-t border-dashed" style="border-color:#A855F7" aria-hidden="true"></div><span class="text-[11px] uppercase font-semibold tracking-widest" style="color:#A855F7">MA 90</span></div>
            <div class="flex items-center gap-2"><div class="w-4 h-0 border-t-2 border-dotted" style="border-color:#64748B" aria-hidden="true"></div><span class="text-[11px] uppercase font-semibold tracking-widest" style="color:#64748B">Volume</span></div>
        </div>
    {/if}

    {#if selectMode}
        <div class="absolute top-4 right-6 z-[110] px-3 py-1.5 bg-orange-500/20 border border-orange-500/40 rounded-lg pointer-events-none" role="status" aria-live="polite">
            <span class="text-[12px] font-semibold text-orange-400 uppercase tracking-wider">Drag to select range</span>
        </div>
    {/if}

    <div bind:this={brushOverlay} class="absolute top-0 bottom-0 z-[105] hidden pointer-events-none bg-orange-500/20 border-l-2 border-r-2 border-orange-500/50" aria-hidden="true"></div>
    <div bind:this={rangeLineStart} class="absolute top-0 bottom-0 z-[106] pointer-events-none hidden" style="width: 0; border-left: 2px dashed rgba(249, 115, 22, 0.6);" aria-hidden="true"></div>
    <div bind:this={rangeLineEnd} class="absolute top-0 bottom-0 z-[106] pointer-events-none hidden" style="width: 0; border-left: 2px dashed rgba(249, 115, 22, 0.6);" aria-hidden="true"></div>
    <div bind:this={tooltip} class="absolute hidden z-[120] pointer-events-none transition-all duration-150" role="tooltip" aria-live="polite"></div>
    <div bind:this={stickyLabel} class="absolute right-0 z-[115] pointer-events-none hidden text-[13px] font-bold tabular-nums text-text bg-bg-card/80 border border-border px-1.5 py-0.5 rounded-l" aria-hidden="true"></div>
</div>

<style>
    :global(.tv-lightweight-charts-attribution) { display: none !important; }

    /* Tooltip classes used in innerHTML — must use :global() */
    :global(.tt-bg) { background: var(--surface-overlay) !important; opacity: 0.97; backdrop-filter: blur(8px); }
    :global(.tt-border) { border-color: var(--border-default) !important; }
    :global(.tt-muted) { color: var(--text-muted) !important; }
    :global(.tt-text) { color: var(--text-primary) !important; }
    :global(.tt-positive) { color: var(--color-positive) !important; }
    :global(.tt-negative) { color: var(--color-negative) !important; }

</style>