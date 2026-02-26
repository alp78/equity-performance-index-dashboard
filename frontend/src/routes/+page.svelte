<!--
  ═══════════════════════════════════════════════════════════════════════════
   +page.svelte — Main Dashboard Orchestrator
  ═══════════════════════════════════════════════════════════════════════════
   Single-page application root that wires together the Sidebar, PriceChart,
   and all bottom/side panels across three display modes:

     • Stock Browsing  — candlestick chart + technicals, rankings, macro
     • Sector Rotation — cross-index or single-index sector comparison
     • Global Macro    — multi-index overview, correlation, news, calendar

   Manages period/range controls, backend startup gate, data loading for
   each mode, and reactive $effect chains that keep panels in sync when
   the user switches index, symbol, sector, or analysis mode.

   Data sources : /data/{symbol}, /index-prices/data, /sector-comparison/*,
                  /metadata/{symbol}, /health (startup gate)
   Layout       : sidebar (left) + main area (header + chart + bottom grid)

   Sections:
     1. APPLICATION STATE       — reactive state for all three modes
     2. SECTOR DATA PIPELINE    — preload + cache sector/industry/stock data
     3. INDUSTRY COMPUTATION    — weighted-average series + return calc
     4. SECTOR FETCH ORCHESTR.  — 3 fast paths + network fallback
     5. REACTIVE EFFECTS        — $effect chains for sector/index/mode sync
     6. PERIOD, RANGE & META    — period buttons, custom range, display name
     7. DATA LOADING            — stock OHLCV, overview comparison, prefetch
     8. LIFECYCLE & STARTUP     — onMount health gate, index/symbol watchers
  ═══════════════════════════════════════════════════════════════════════════
-->
<script>
    import { selectedSymbol, loadSummaryData, marketIndex, currentCurrency, INDEX_CONFIG, summaryData, isOverviewMode, INDEX_TICKER_MAP, INDEX_KEY_TO_TICKER, loadIndexOverviewData, isSectorMode, isMacroContextMode, sectorSelectedIndices, singleSelectedIndex, selectedSector, sectorAnalysisMode, selectedIndustries, crossSelectedIndustries, singleModeIndustries, selectedSectors, sectorsByIndex, getCached, setCached, isCacheFresh, macroHighlightIndex, macroHighlightPair, ALL_SECTORS, sectorHighlightEnabled } from '$lib/stores.js';
    import { API_BASE_URL } from '$lib/config.js';
    import Sidebar from '$lib/components/Sidebar.svelte';
    import PriceChart from '$lib/components/PriceChart.svelte';
    import TopMovers from '$lib/components/TopMovers.svelte';
    import IndexPerformanceTable from '$lib/components/IndexPerformanceTable.svelte';
    import NewsFeed from '$lib/components/NewsFeed.svelte';
    import SectorHeatmap from '$lib/components/SectorHeatmap.svelte';
    import SectorTopStocks from '$lib/components/SectorTopStocks.svelte';
    import SectorRankings from '$lib/components/SectorRankings.svelte';
    import IndustryBreakdown from '$lib/components/IndustryBreakdown.svelte';
    import MostActive from '$lib/components/MostActive.svelte';
    import CorrelationHeatmap from '$lib/components/CorrelationHeatmap.svelte';
    import EconomicCalendar from '$lib/components/EconomicCalendar.svelte';
    import MacroWatchlist from '$lib/components/MacroWatchlist.svelte';
    import RiskDashboard from '$lib/components/RiskDashboard.svelte';
    import TechnicalLevels from '$lib/components/TechnicalLevels.svelte';
    import StockMetrics from '$lib/components/StockMetrics.svelte';
    import Card from '$lib/components/ui/Card.svelte';
    import ThemeToggle from '$lib/components/ui/ThemeToggle.svelte';
    import { Newspaper, Globe, PieChart, TrendingUp, Info } from 'lucide-svelte';
    import Tooltip from '$lib/components/ui/Tooltip.svelte';
    import { onMount, untrack } from 'svelte';
    import { INDEX_COLORS, SECTOR_PALETTE, SECTOR_INDEX_NAMES, SECTOR_ABBREV, getSectorColor } from '$lib/theme.js';
    import { MARKET_HOURS, SYMBOL_MARKET_MAP } from '$lib/index-registry.js';

    // ─── Fetch Utilities ───

    async function fetchWithTimeout(url, timeout = 10000, opts = {}) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);
        try {
            const response = await fetch(url, { ...opts, signal: controller.signal });
            clearTimeout(timeoutId);
            return response;
        } catch (error) {
            clearTimeout(timeoutId);
            if (error.name === 'AbortError') throw new Error(`Timeout`);
            throw error;
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  1. APPLICATION STATE
    // ═══════════════════════════════════════════════════════════════════════
    //  All reactive state for the three display modes: stock browsing (chart
    //  data, loading flags), overview (comparison series, macro highlight),
    //  and sector rotation (sector series, industry cache, top stocks).

    // ─── Stock & Comparison State ───

    let fullStockData = $state([]);
    let allComparisonData = $state(null);
    let comparisonData = $state(null);
    let currencyMode = $state('local'); // 'local' or 'usd'
    let fxError = $state(false);
    let highlightKey = $derived($macroHighlightIndex);
    let highlightTicker = $derived(highlightKey ? INDEX_KEY_TO_TICKER[highlightKey] : null);

    // when an index is selected, clear the correlation pair filter
    $effect(() => {
        if (highlightKey) macroHighlightPair.set(null);
    });

    // sync comparisonData from allComparisonData, filtering to pair if selected
    $effect(() => {
        const all = allComparisonData;
        if (!all || !all.series) { comparisonData = null; return; }
        if ($macroHighlightPair) {
            const filtered = all.series.filter(s =>
                s.indexKey === $macroHighlightPair.row || s.indexKey === $macroHighlightPair.col
            );
            comparisonData = filtered.length > 0 ? { ...all, series: filtered } : all;
        } else {
            comparisonData = all;
        }
    });
    let isInitialLoading = $state(true);
    let isIndexSwitching = $state(false);
    let inOverview = $derived($isOverviewMode);
    let inSectors = $derived($isSectorMode);
    let inContext = $derived($isMacroContextMode);
    let contextEverVisited = $state(false);
    $effect(() => { if (inContext) contextEverVisited = true; });

    // ─── Macro Context — live ticker quotes (BTC, Gold, EUR/USD) ───
    let macroTickerQuotes = $state({});
    let _macroTickerWs = null;
    let _btcInterval = null;
    let _fxInterval = null;
    let _macroTickerAbort = null;

    // BTC: direct Binance fetch every 3s for near-real-time price
    async function fetchBtcDirect() {
        try {
            const [tickerR, klineR] = await Promise.all([
                fetch('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT'),
                fetch('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1'),
            ]);
            if (tickerR.ok && klineR.ok) {
                const ticker = await tickerR.json();
                const kline = await klineR.json();
                const price = parseFloat(ticker.price);
                const openPrice = parseFloat(kline[0][1]);
                const diff = price - openPrice;
                const pct = openPrice !== 0 ? (diff / openPrice) * 100 : 0;
                macroTickerQuotes['BINANCE:BTCUSDT'] = { price: Math.round(price * 100) / 100, pct: Math.round(pct * 100) / 100, diff: Math.round(diff * 100) / 100 };
            }
        } catch {}
    }

    // XAU/EUR: fetch from backend every 60s
    async function fetchFxTicker() {
        _macroTickerAbort?.abort();
        _macroTickerAbort = new AbortController();
        try {
            const res = await fetch(`${API_BASE_URL}/market-data`, { signal: _macroTickerAbort.signal });
            if (res.ok) {
                const data = await res.json();
                for (const s of ['FXCM:XAU/USD', 'FXCM:EUR/USD']) {
                    const clean = s.split(':')[1];
                    for (const u of Object.values(data)) {
                        if (u.symbol === s || u.symbol === clean) {
                            macroTickerQuotes[s] = { price: u.price, pct: u.pct, diff: u.diff };
                        }
                    }
                }
            }
        } catch (err) {
            if (err.name !== 'AbortError') console.error('FX ticker fetch error:', err);
        }
    }

    $effect(() => {
        if (!inContext) return;
        // Initial fetch for all 3
        fetchBtcDirect();
        fetchFxTicker();
        // BTC: 3s interval
        _btcInterval = setInterval(fetchBtcDirect, 3000);
        // XAU/EUR: 60s interval
        _fxInterval = setInterval(fetchFxTicker, 60000);
        return () => {
            clearInterval(_btcInterval);
            clearInterval(_fxInterval);
            _macroTickerAbort?.abort();
        };
    });

    // Market status for macro tickers
    function macroTickerStatus(symbol) {
        const mkey = SYMBOL_MARKET_MAP[symbol];
        const market = MARKET_HOURS[mkey];
        if (!market) return { label: '', color: '', dot: '', pulse: false };
        if (!market.tz) return { label: 'LIVE', color: 'text-up', dot: 'bg-up', pulse: true }; // crypto always open
        const now = new Date();
        const tz = new Date(now.toLocaleString('en-US', { timeZone: market.tz }));
        const day = tz.getDay(), mins = tz.getHours() * 60 + tz.getMinutes();
        let open = false;
        if (market.sundayOpen) {
            open = !(day === 6 || (day === 0 && mins < market.open.h * 60 + market.open.m) || (day === 5 && mins >= market.close.h * 60 + market.close.m));
        } else {
            if (day >= 1 && day <= 5) {
                open = mins >= market.open.h * 60 + market.open.m && mins < market.close.h * 60 + market.close.m;
            }
        }
        return open
            ? { label: 'LIVE', color: 'text-up', dot: 'bg-up', pulse: true }
            : { label: 'CLOSED', color: 'text-down', dot: 'bg-down', pulse: false };
    }

    // ─── Backend Startup Gate ───
    // poll /health until backend reports ready before loading any data

    let backendReady = $state(false);
    let startupProgress = $state('');
    let startupDone = $state([]);
    let startupLoading = $state([]);

    // ─── Sector Comparison State ───

    let sectorComparisonData = $state(null);
    let sectorDataLoading = $state(false);
    let _lastSectorFetchKey = '';

    // precomputed sector series for all indices — enables instant switching without network calls
    let allSectorSeries = $state(null);
    let allSectorSeriesLoading = $state(false);

    // per-industry series cache keyed by { sector: { indexKey: { industry: [{time, pct, n}] } } }
    let industrySeriesCache = $state({});
    let industrySeriesLoading = $state(false);

    // precomputed stock returns keyed by { indexKey: [{symbol, name, industry, sector, return_pct}] }
    let topStocksCache = $state(null);
    let _topStocksCachePeriod = '';

    // ─── Sector Display Constants ───

    // SECTOR_ABBREV imported from theme.js; ALL_SECTORS from stores.js

    // map sector/index keys to colors depending on analysis mode
    let sectorCompareColors = $derived((() => {
        if ($sectorAnalysisMode === 'single-index') {
            const colors = {};
            $selectedSectors.forEach(sec => { colors[sec] = getSectorColor(sec); });
            return colors;
        }
        return INDEX_COLORS;
    })());

    // map sector/index keys to display names depending on analysis mode
    let sectorCompareNames = $derived((() => {
        if ($sectorAnalysisMode === 'single-index') {
            return Object.fromEntries($selectedSectors.map(s => [s, SECTOR_ABBREV[s] || s]));
        }
        return SECTOR_INDEX_NAMES;
    })());

    let _sectorDataVersion = 0;
    let _allSeriesVersion = $state(0);

    // ═══════════════════════════════════════════════════════════════════════
    //  2. SECTOR DATA PIPELINE
    // ═══════════════════════════════════════════════════════════════════════
    //  Three preload functions fetch and cache sector/industry/stock data
    //  from the backend.  Each retries pending indices with escalating delays
    //  so switching between indices is instant once the initial load completes.

    // ─── Sector Series Preload ───
    // fetch and cache all sector time series for every index at once
    async function preloadAllSectorSeries() {
        if (allSectorSeriesLoading || allSectorSeries) return;
        allSectorSeriesLoading = true;
        try {
            const res = await fetchWithTimeout(
                `${API_BASE_URL}/sector-comparison/all-series?indices=all`, 30000
            );
            if (res.ok) {
                const json = await res.json();
                allSectorSeries = json.data || {};

                // retry pending indices with increasing intervals until all are ready
                if (json.pending && json.pending.length > 0) {
                    let pending = [...json.pending];
                    let attempt = 0;
                    const maxAttempts = 6;
                    const delays = [5000, 8000, 12000, 15000, 20000, 30000];
                    const retryPending = async () => {
                        if (pending.length === 0 || attempt >= maxAttempts) return;
                        try {
                            const retryRes = await fetchWithTimeout(
                                `${API_BASE_URL}/sector-comparison/all-series?indices=${pending.join(',')}`, 30000
                            );
                            if (retryRes.ok) {
                                const retryJson = await retryRes.json();
                                if (retryJson.data && Object.keys(retryJson.data).length > 0) {
                                    allSectorSeries = { ...allSectorSeries, ...retryJson.data };
                                    _allSeriesVersion++;
                                    _lastSectorFetchKey = '';
                                }
                                pending = retryJson.pending || [];
                            }
                        } catch {}
                        if (pending.length > 0) {
                            attempt++;
                            setTimeout(retryPending, delays[Math.min(attempt, delays.length - 1)]);
                        }
                    };
                    setTimeout(retryPending, delays[0]);
                }

                // table caches are fetched on demand by sub-components — no pre-warm needed
            }
        } catch {}
        allSectorSeriesLoading = false;
    }

    // ─── Industry Series Loading ───
    // fetch industry-level time series for a sector, merging into the cache.
    // _industrySeriesRequestedSet is never cleared for the same key — prevents re-requests
    // that would otherwise cause infinite reactive loops (cache update → effect re-fire → re-request).
    let _industrySeriesRequestedSet = new Set();
    let _industrySeriesCacheVersion = $state(0);
    async function loadIndustrySeries(sector, indices) {
        if (!sector || !indices || indices.length === 0) return;
        const sortedIndices = [...indices].sort();
        const key = `${sector}_${sortedIndices.join(',')}`;
        if (_industrySeriesRequestedSet.has(key)) return;
        _industrySeriesRequestedSet.add(key);
        industrySeriesLoading = true;
        try {
            const res = await fetchWithTimeout(
                `${API_BASE_URL}/sector-comparison/industry-series?sector=${encodeURIComponent(sector)}&indices=${indices.join(',')}`,
                20000
            );
            if (res.ok) {
                const json = await res.json();
                if (json.data) {
                    industrySeriesCache = {
                        ...industrySeriesCache,
                        [sector]: { ...(industrySeriesCache[sector] || {}), ...json.data },
                    };
                    _industrySeriesCacheVersion++;
                }
            }
        } catch {}
        industrySeriesLoading = false;
    }

    // ─── Top Stocks Preloading ───
    // preload all stock returns for a given period from precomputed backend tables
    async function preloadTopStocks(period) {
        if (!period || _topStocksCachePeriod === period) return;
        _topStocksCachePeriod = period;
        try {
            const res = await fetchWithTimeout(
                `${API_BASE_URL}/sector-comparison/all-top-stocks?period=${period}`, 15000
            );
            if (res.ok) {
                const json = await res.json();
                if (json.data) {
                    topStocksCache = json.data;
                    // retry pending indices with escalating delays
                    if (json.pending && json.pending.length > 0) {
                        let pending = [...json.pending];
                        let attempt = 0;
                        const maxAttempts = 5;
                        const delays = [5000, 8000, 12000, 15000, 20000];
                        const retryPending = async () => {
                            if (pending.length === 0 || attempt >= maxAttempts) return;
                            if (_topStocksCachePeriod !== period) return; // period changed, abort
                            try {
                                const retryRes = await fetchWithTimeout(
                                    `${API_BASE_URL}/sector-comparison/all-top-stocks?period=${period}`, 15000
                                );
                                if (retryRes.ok) {
                                    const retryJson = await retryRes.json();
                                    if (retryJson.data && Object.keys(retryJson.data).length > 0) {
                                        topStocksCache = { ...topStocksCache, ...retryJson.data };
                                    }
                                    pending = retryJson.pending || [];
                                }
                            } catch {}
                            if (pending.length > 0) {
                                attempt++;
                                setTimeout(retryPending, delays[Math.min(attempt, delays.length - 1)]);
                            }
                        };
                        setTimeout(retryPending, delays[0]);
                    }
                }
            }
        } catch {}
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  3. INDUSTRY SERIES COMPUTATION
    // ═══════════════════════════════════════════════════════════════════════
    //  Pure functions that derive weighted-average time series from selected
    //  industries and compute return % over a period.  Results are LRU-cached
    //  so toggling industry filters is instant.

    // produce a weighted-average time series from selected industries (weight = stock count per day)
    // LRU cache for weighted series so toggling back to a prior selection is instant
    let _weightedCache = {};
    let _weightedCacheSize = 0;

    function computeWeightedIndustrySeries(indexKey, sector, selectedInds) {
        const sectorCache = industrySeriesCache[sector];
        if (!sectorCache) return null;
        const indexData = sectorCache[indexKey];
        if (!indexData) return null;

        // check result cache
        const cacheKey = `${indexKey}_${sector}_${selectedInds.join('|')}`;
        if (_weightedCache[cacheKey]) return _weightedCache[cacheKey];

        const selectedSeries = selectedInds.map(ind => indexData[ind]).filter(Boolean);
        if (selectedSeries.length === 0) return null;
        if (selectedSeries.length === 1) {
            // return original points directly (no copy needed, data is immutable)
            const result = selectedSeries[0];
            _weightedCache[cacheKey] = result;
            return result;
        }

        // build unified timeline from all selected industries
        const allTimes = new Set();
        for (const series of selectedSeries) {
            for (const pt of series) allTimes.add(pt.time);
        }
        const times = [...allTimes].sort();

        // forward-fill each industry series to the unified timeline
        // so composition doesn't change date-to-date (avoids spikes)
        const filledSeries = selectedSeries.map(series => {
            const map = new Map();
            for (const pt of series) map.set(pt.time, pt);
            const filled = new Array(times.length);
            let lastPct = null;
            let lastN = null;
            for (let i = 0; i < times.length; i++) {
                const pt = map.get(times[i]);
                if (pt) { lastPct = pt.pct; lastN = pt.n; }
                filled[i] = lastPct !== null ? { pct: lastPct, n: lastN } : null;
            }
            return filled;
        });

        // compute weighted average across forward-filled series
        const result = [];
        for (let i = 0; i < times.length; i++) {
            let weightedSum = 0;
            let totalWeight = 0;
            for (const filled of filledSeries) {
                const pt = filled[i];
                if (pt) {
                    weightedSum += pt.pct * pt.n;
                    totalWeight += pt.n;
                }
            }
            if (totalWeight > 0) {
                result.push({ time: times[i], pct: weightedSum / totalWeight });
            }
        }

        // cap cache at 100 entries to bound memory
        if (_weightedCacheSize > 100) { _weightedCache = {}; _weightedCacheSize = 0; }
        _weightedCache[cacheKey] = result;
        _weightedCacheSize++;
        return result;
    }

    // compute return over a period by rebasing: ((1 + end%) / (1 + start%) - 1) * 100
    function computeFilteredReturn(series, period, customRng) {
        if (!series || series.length < 2) return null;

        let startIdx = 0;
        let endIdx = series.length - 1;

        if (customRng?.start) {
            startIdx = series.findIndex(p => p.time >= customRng.start);
            const endI = series.findIndex(p => p.time > customRng.end);
            endIdx = endI >= 0 ? endI - 1 : series.length - 1;
            if (startIdx < 0) startIdx = 0;
        } else if (period && period.toLowerCase() !== 'max') {
            const INTERVALS = { '1w': 7, '1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '5y': 1825 };
            const days = INTERVALS[period.toLowerCase()] || 365;
            const cutoff = new Date();
            cutoff.setDate(cutoff.getDate() - days);
            const cutoffStr = cutoff.toISOString().slice(0, 10);
            startIdx = series.findIndex(p => p.time >= cutoffStr);
            if (startIdx < 0) startIdx = 0;
        }

        const startPct = series[startIdx].pct;
        const endPct = series[endIdx].pct;
        return ((1 + endPct / 100) / (1 + startPct / 100) - 1) * 100;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  4. SECTOR FETCH ORCHESTRATION
    // ═══════════════════════════════════════════════════════════════════════
    //  Resolves sector chart data via 3 fast paths (allSectorSeries cache,
    //  industry weighted cache, per-sector mixed cache) before falling back
    //  to a network call.  Each path updates sectorComparisonData for PriceChart.

    // resolve sector chart data from cache (fast path) or network (slow path)
    async function fetchSectorData(sector, indices, mode, industries, sectors, singleFilters = {}) {
        if (!indices || indices.length === 0) return;

        let sectorParam, indicesParam, industriesParam;
        if (mode === 'single-index') {
            if (!sectors || sectors.length === 0) return;
            sectorParam = [...sectors].sort().join(',');
            indicesParam = [...indices].sort().join(',');
        } else {
            if (!sector) return;
            sectorParam = sector;
            indicesParam = [...indices].sort().join(',');
        }
        industriesParam = industries && industries.length > 0 ? [...industries].sort().join(',') : '';

        // single-index: include all per-sector filters in the dedup key so every toggle re-evaluates
        let allFiltersKey = '';
        if (mode === 'single-index') {
            allFiltersKey = Object.entries(singleFilters)
                .filter(([, v]) => v.length > 0)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([k, v]) => `${k}:${[...v].sort().join('+')}`)
                .join('|');
        }
        const fetchKey = `${mode}_${sectorParam}_${indicesParam}_${industriesParam}_${allFiltersKey}_v${_allSeriesVersion}`;
        if (fetchKey === _lastSectorFetchKey && sectorComparisonData) return;

        _lastSectorFetchKey = fetchKey;

        // fast path: serve from the preloaded allSectorSeries cache (no network).
        // skip for single-index when any sector has a per-sector industry filter.
        const hasAnySingleFilter = mode === 'single-index' && Object.values(singleFilters).some(v => v && v.length > 0);
        if (!industriesParam && !hasAnySingleFilter && allSectorSeries) {
            const series = [];
            let allHit = true;

            if (mode === 'cross-index') {
                for (const idx of indices) {
                    const idxData = allSectorSeries[idx];
                    if (!idxData || !idxData[sector]) { allHit = false; continue; }
                    series.push({ symbol: idx, points: idxData[sector] });
                }
            } else if (mode === 'single-index') {
                const idx = indices[0];
                const idxData = allSectorSeries[idx];
                if (!idxData) {
                    allHit = false;
                } else {
                    for (const sec of sectors) {
                        if (!idxData[sec]) continue;
                        series.push({ symbol: sec, points: idxData[sec] });
                    }
                    if (series.length === 0) allHit = false;
                }
            }

            if (series.length > 0) {
                _sectorDataVersion++;
                sectorComparisonData = { series, _version: _sectorDataVersion };
                sectorDataLoading = false;
                // if some indices are pending, the retry in preloadAllSectorSeries will
                // update allSectorSeries, triggering a re-fire that fills in the rest.
                return;
            }
            // all cached indices checked but none had data — mark as empty instead of loading
            const allCached = mode === 'cross-index'
                ? indices.every(idx => allSectorSeries[idx])
                : allSectorSeries[indices[0]];
            if (allCached) {
                _sectorDataVersion++;
                sectorComparisonData = { series: [], _version: _sectorDataVersion, _empty: true };
                sectorDataLoading = false;
                return;
            }
        }

        // fast path 2: industry filter active — compute weighted series from industrySeriesCache
        if (industriesParam) {
            const sectorForIndustry = mode === 'cross-index' ? sector : null;
            // cross-index: blend selected industries per index
            if (mode === 'cross-index' && industrySeriesCache[sector]) {
                const series = [];
                let allHit = true;
                for (const idx of indices) {
                    const weighted = computeWeightedIndustrySeries(idx, sector, industries);
                    if (!weighted || weighted.length < 2) { allHit = false; break; }
                    series.push({ symbol: idx, points: weighted });
                }
                if (allHit && series.length > 0) {
                    _sectorDataVersion++;
                    sectorComparisonData = { series, _version: _sectorDataVersion };
                    sectorDataLoading = false;
                    return;
                }
            }
            // cache miss — fetch on demand, show unfiltered chart data in the meantime.
            // when loadIndustrySeries completes, _industrySeriesCacheVersion increments,
            // the main effect re-fires, and fetchSectorData hits fast path 2.
            if (mode === 'cross-index') {
                if (sector) loadIndustrySeries(sector, indices);
            }
        }

        // fast path 3: single-index with per-sector industry filters
        if (mode === 'single-index' && hasAnySingleFilter && allSectorSeries) {
            const idx = indices[0];
            const hasSectorCache = allSectorSeries[idx];
            if (hasSectorCache) {
                const series = [];
                let allHit = true;
                let needsLoad = [];
                for (const sec of sectors) {
                    const secFilter = singleFilters[sec] || [];
                    if (secFilter.length > 0) {
                        // this sector has an industry filter — use weighted series if cached
                        if (industrySeriesCache[sec]?.[idx]) {
                            const weighted = computeWeightedIndustrySeries(idx, sec, secFilter);
                            if (weighted && weighted.length >= 2) {
                                series.push({ symbol: sec, points: weighted });
                                continue;
                            }
                        }
                        // cache miss for this sector — trigger on-demand load
                        needsLoad.push(sec);
                        allHit = false;
                    }
                    // no filter or cache miss — use unfiltered sector data
                    const secData = hasSectorCache[sec];
                    if (secData) series.push({ symbol: sec, points: secData });
                }
                // trigger on-demand loads for sectors missing industry cache
                for (const sec of needsLoad) loadIndustrySeries(sec, indices);
                if (allHit && series.length > 0) {
                    _sectorDataVersion++;
                    sectorComparisonData = { series, _version: _sectorDataVersion };
                    sectorDataLoading = false;
                    return;
                }
                // partial hit — still show what we have (mix of filtered and unfiltered)
                if (series.length > 0) {
                    _sectorDataVersion++;
                    sectorComparisonData = { series, _version: _sectorDataVersion };
                    sectorDataLoading = false;
                    return;
                }
            }
        }

        // slow path: network fetch when caches cannot satisfy the request
        sectorDataLoading = true;
        try {
            let url = `${API_BASE_URL}/sector-comparison/data-v2?sector=${encodeURIComponent(sectorParam)}&indices=${indicesParam}&mode=${mode}&period=max`;
            if (industriesParam) url += `&industries=${encodeURIComponent(industriesParam)}`;
            const res = await fetchWithTimeout(url, 15000);
            if (res.ok) {
                const data = await res.json();
                if (data.series && data.series.length > 0) {
                    _sectorDataVersion++;
                    sectorComparisonData = {
                        series: data.series.map(s => ({
                            symbol: s.symbol,
                            points: s.points,
                        })),
                        _version: _sectorDataVersion,
                    };
                } else {
                    sectorComparisonData = { series: [], _version: ++_sectorDataVersion, _empty: true };
                }
            }
        } catch {}
        sectorDataLoading = false;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  5. REACTIVE EFFECTS
    // ═══════════════════════════════════════════════════════════════════════
    //  Svelte $effect chains that keep panels in sync: sector/index/mode
    //  changes trigger data fetches, preloads fire on mode entry, and cache
    //  version counters cause re-evaluation when background retries complete.

    // track previous values to detect real changes.
    // _lastSelectedSector starts empty so the first effect run syncs selectedIndustries
    // from the per-sector map (prevents stale industry data persisted from a previous session).
    let _lastSelectedSector = '';
    let _lastSectorIndex = ($singleSelectedIndex || [])[0] || '';
    let _lastMode = $sectorAnalysisMode;


    // respond to sector/index/mode/industry selection changes
    // gated on allSectorSeries so the chart waits for cached data instead of firing a slow-path API call
    $effect(() => {
        if (!backendReady) return;
        if (!inSectors) return;
        if (!allSectorSeries) return;
        const sector = $selectedSector;
        const indices = $sectorAnalysisMode === 'single-index' ? $singleSelectedIndex : $sectorSelectedIndices;
        const mode = $sectorAnalysisMode;
        if (mode !== _lastMode) {
            _lastMode = mode;
        }
        const industries = $selectedIndustries;
        const singleFilters = $singleModeIndustries;
        let sectors = $selectedSectors;

        // on index switch in single-index mode, save current sector selection and restore the saved one
        if (mode === 'single-index') {
            const currentIndex = (indices || [])[0] || '';
            if (currentIndex !== _lastSectorIndex) {
                if (_lastSectorIndex) {
                    sectorsByIndex.update(m => ({ ...m, [_lastSectorIndex]: sectors }));
                }
                _lastSectorIndex = currentIndex;
                const saved = $sectorsByIndex[currentIndex];
                const next = saved && saved.length > 0 ? saved : ALL_SECTORS;
                selectedSectors.set(next);
                sectors = next;
            }
        }

        // on sector change: sync selectedIndustries from per-sector map (cross-index)
        // or clear (single-index, handled separately by its own sync effect).
        // also reset all OTHER sectors' industry selections so they revert to "all".
        if (sector !== _lastSelectedSector) {
            _lastSelectedSector = sector;
            if (mode === 'cross-index') {
                // clear custom industry selections for every sector except the new one
                const current = $crossSelectedIndustries;
                const hasOthers = Object.keys(current).some(k => k !== sector && current[k].length > 0);
                if (hasOthers) {
                    const cleaned = {};
                    if (current[sector]?.length > 0) cleaned[sector] = current[sector];
                    crossSelectedIndustries.set(cleaned);
                }
                const perSectorFilter = current[sector] || [];
                if (JSON.stringify(industries) !== JSON.stringify(perSectorFilter)) {
                    selectedIndustries.set(perSectorFilter);
                    return;
                }
            }
            // single-index: no save/restore needed here — the Sidebar manages per-sector
            // filters via singleModeIndustries store and syncs them to selectedIndustries
        }

        // track cache versions so this effect re-fires when new data arrives from retries.
        // _allSeriesVersion increments when preloadAllSectorSeries retries bring in more indices.
        // _industrySeriesCacheVersion increments when loadIndustrySeries completes.
        const _asv = _allSeriesVersion;
        const _icv = _industrySeriesCacheVersion;
        if (_icv > 0) _lastSectorFetchKey = '';

        // untrack prevents cache reads inside fetchSectorData from re-triggering this effect
        untrack(() => fetchSectorData(sector, indices, mode, industries, sectors, singleFilters));
    });

    // preload all sector series on first entry into sector mode
    $effect(() => {
        if (backendReady && inSectors && !allSectorSeries && !allSectorSeriesLoading) {
            preloadAllSectorSeries();
        }
    });

    // preload industry series when a sector is selected so toggles are instant from the start
    $effect(() => {
        if (!backendReady || !inSectors) return;
        const sector = $selectedSector;
        const mode = $sectorAnalysisMode;
        const indices = mode === 'single-index' ? $singleSelectedIndex : $sectorSelectedIndices;
        if (!sector || !indices || indices.length === 0) return;
        untrack(() => loadIndustrySeries(sector, indices));
    });

    // preload all stock returns for cross-index mode so top stocks update instantly.
    // re-fetches when period changes (custom ranges fall back to API in SectorTopStocks).
    $effect(() => {
        if (!backendReady || !inSectors || $sectorAnalysisMode !== 'cross-index') return;
        const period = currentPeriod;
        if (!period) return; // custom range — skip preload
        preloadTopStocks(period);
    });

    // ═══════════════════════════════════════════════════════════════════════
    //  6. PERIOD, RANGE & METADATA
    // ═══════════════════════════════════════════════════════════════════════
    //  Period buttons (1W–MAX) and custom date range selection, persisted to
    //  sessionStorage.  Also resolves the stock display name via metadata API.

    // ─── Period / Range State ───
    // read persisted period from sessionStorage synchronously to prevent button flash on load
    let initialPeriod = '5y';
    let initialRange = null;

    if (typeof window !== 'undefined') {
        const savedPeriod = sessionStorage.getItem('chart_period');
        const savedRange = sessionStorage.getItem('chart_custom_range');
        if (savedRange) {
            try {
                initialRange = JSON.parse(savedRange);
                initialPeriod = null;
            } catch (e) {
                if (savedPeriod) initialPeriod = savedPeriod;
            }
        } else if (savedPeriod) {
            initialPeriod = savedPeriod;
        }
    }

    let currentPeriod = $state(initialPeriod);
    let customRange = $state(initialRange);
    let selectMode = $state(false);

    // ─── Metadata & Display Name ───

    let metadataCache = {};
    let metadataFetchId = 0;
    let stockFetchId = 0;
    let stockFetchAbort = null;    // AbortController to cancel in-flight HTTP requests
    let lastFetchedSymbol = '';
    let lastFetchedIndex = '';
    let displayNameText = $state('');

    let currentSymbol = $derived($selectedSymbol);
    let ccy = $derived($currentCurrency);
    let assets = $derived($summaryData.assets || []);

    // resolve display name: prefer metadata API, fall back to summary assets, then empty (h1 shows symbol)
    let displayName = $derived((() => {
        if (displayNameText) return displayNameText;
        const asset = assets.find(a => a.symbol === currentSymbol);
        if (asset && asset.name && asset.name !== 0 && asset.name !== currentSymbol) return asset.name;
        return '';
    })());

    let stockSectorName = $derived((() => {
        const asset = assets.find(a => a.symbol === currentSymbol);
        if (!asset) return '';
        return (asset.sector && asset.sector !== 0) ? asset.sector : '';
    })());

    let stockSectorIndustry = $derived((() => {
        const asset = assets.find(a => a.symbol === currentSymbol);
        if (!asset) return '';
        const sec = (asset.sector && asset.sector !== 0) ? asset.sector : '';
        const ind = (asset.industry && asset.industry !== 0) ? asset.industry : '';
        if (sec && ind) return `${sec}: ${ind}`;
        return sec || ind || '';
    })());

    // ─── Period / Range Helpers ───

    function fmtDate(d) {
        if (!d) return '';
        const dt = new Date(d + 'T00:00:00');
        const day = dt.getDate();
        const mon = dt.toLocaleDateString('en-GB', { month: 'short' });
        const yr = String(dt.getFullYear()).slice(2);
        return `${day} ${mon} '${yr}`;
    }

    function handleResetPeriod() {
        if (customRange) { customRange = null; selectMode = false; }
        const p = sessionStorage.getItem('chart_period') || '5y';
        currentPeriod = null;
        setTimeout(() => { currentPeriod = p; }, 10); // force re-render even if same value
        sessionStorage.removeItem('chart_custom_range');
    }

    function setPeriod(p) {
        currentPeriod = p;
        customRange = null;
        selectMode = false;
        sessionStorage.setItem('chart_period', p);
        sessionStorage.removeItem('chart_custom_range');
    }

    function toggleCustomMode() {
        if (selectMode && customRange) { selectMode = false; customRange = null; return; }
        if (selectMode) { selectMode = false; return; }
        selectMode = true;
        customRange = null;
    }

    function handleRangeSelect(range) {
        customRange = range;
        selectMode = false;
        currentPeriod = null;
        sessionStorage.setItem('chart_custom_range', JSON.stringify(range));
        sessionStorage.removeItem('chart_period');
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  7. DATA LOADING
    // ═══════════════════════════════════════════════════════════════════════
    //  HTTP fetchers for stock OHLCV data, overview comparison series, and
    //  background prefetch of macro widgets.  Stock fetch retries with backoff;
    //  overview data uses SWR from the shared cache layer.

    // ─── Stock Data ───

    async function fetchStockData(symbol) {
        if (!symbol) return;
        const myFetchId = ++stockFetchId;

        // Abort any in-flight stock data request
        stockFetchAbort?.abort();
        stockFetchAbort = new AbortController();
        const signal = stockFetchAbort.signal;

        // fire-and-forget metadata fetch to populate displayNameText
        const metaId = ++metadataFetchId;
        fetchWithTimeout(`${API_BASE_URL}/metadata/${encodeURIComponent(symbol)}`, 8000)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.name && metaId === metadataFetchId) {
                    metadataCache[symbol] = data.name;
                    displayNameText = data.name;
                }
            })
            .catch(() => {});

        // retry with backoff — backend may be lazy-loading the index on first request
        const maxRetries = 3;
        try {
            for (let attempt = 0; attempt < maxRetries; attempt++) {
                if (myFetchId !== stockFetchId) return;
                try {
                    const res = await fetchWithTimeout(
                        `${API_BASE_URL}/data/${encodeURIComponent(symbol)}?period=max&t=${Date.now()}`, 15000,
                        { signal }
                    );
                    if (myFetchId !== stockFetchId) return;
                    if (res.ok) {
                        const json = await res.json();
                        if (myFetchId !== stockFetchId) return;
                        if (json && json.length > 0) {
                            fullStockData = json;
                            return;
                        }
                        if (attempt < maxRetries - 1) {
                            await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
                            continue;
                        }
                    }
                } catch (e) {
                    if (signal.aborted || myFetchId !== stockFetchId) return;
                    if (attempt < maxRetries - 1) {
                        await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
                        continue;
                    }
                    console.error(`Failed to fetch data for ${symbol}:`, e);
                }
            }
        } finally {
            // Always reset loading flags for the winning fetch — prevents stuck spinners
            // when rapid switching causes stale fetches to bail out silently
            if (myFetchId === stockFetchId) {
                isInitialLoading = false;
                isIndexSwitching = false;
            }
        }
    }

    // ─── Industry Return Overrides ───
    // single-index mode: override ranking values with industry-filtered returns
    // single-index: override ranking rows for sectors with industry filters.
    // depends on $selectedIndustries so it re-fires when the active sector's filter changes;
    // reads $singleModeIndustries reactively so it re-fires when per-sector filters change.
    let rankingsIndustryOverride = $derived((() => {
        const perSector = $singleModeIndustries;
        const idx = ($singleSelectedIndex || [])[0];
        if (!idx || !perSector) return null;
        const overrides = {};
        for (const [sec, filter] of Object.entries(perSector)) {
            if (!filter || filter.length === 0) continue;
            const weighted = computeWeightedIndustrySeries(idx, sec, filter);
            if (!weighted || weighted.length < 2) continue;
            const ret = computeFilteredReturn(weighted, currentPeriod, customRange);
            if (ret !== null) overrides[sec] = Math.round(ret * 10) / 10;
        }
        return Object.keys(overrides).length > 0 ? overrides : null;
    })());

    // ─── Background Prefetch ───

    /** Prefetch all macro widget data into cache so they hydrate instantly on first mount. */
    function prefetchMacroWidgets() {
        const prefetches = [
            ['news_feed',     '/news',               5 * 60 * 1000,  d => d?.length > 0],
            ['eco_calendar',  '/macro/calendar',     15 * 60 * 1000, d => !!d],
            ['macro_risk',    '/macro/risk-summary', 5 * 60 * 1000,  d => !!d],
            ['macro_fx',      '/macro/fx',           5 * 60 * 1000,  d => !!(d?.rates),       d => d.rates],
            ['macro_rates',   '/macro/rates',        60 * 60 * 1000, d => !!(d?.instruments), d => d.instruments],
        ];
        for (const [key, path, ttl, valid, transform] of prefetches) {
            if (isCacheFresh(key)) continue;
            fetch(`${API_BASE_URL}${path}`).then(r => r.ok ? r.json() : null).then(data => {
                if (data && valid(data)) setCached(key, transform ? transform(data) : data, ttl);
            }).catch(() => {});
        }
    }

    // ─── Overview Data ───
    // fetch all 6 indices at once — client-side filtering keeps it responsive
    const COMPARISON_TTL = 5 * 60 * 1000; // 5 min

    async function fetchComparisonData() {
        const mode = currencyMode;
        const cacheKey = mode === 'usd' ? 'comparison_data_usd' : 'comparison_data';

        // Serve cached data instantly (mode switch / browser refresh)
        const cached = getCached(cacheKey);
        if (cached && !allComparisonData) {
            allComparisonData = cached.data;
            fxError = cached.data?.fxError || false;
        }
        // Skip network if still fresh
        if (isCacheFresh(cacheKey)) return;

        const allTickers = Object.keys(INDEX_TICKER_MAP);
        const currencyParam = mode === 'usd' ? '&currency=usd' : '';
        try {
            const res = await fetchWithTimeout(
                `${API_BASE_URL}/index-prices/data?symbols=${encodeURIComponent(allTickers.join(','))}&period=max${currencyParam}`,
                20000
            );
            if (res.ok) {
                const data = await res.json();
                allComparisonData = data;
                fxError = data?.fxError || false;
                setCached(cacheKey, data, COMPARISON_TTL);
            }
        } catch (e) {
            console.error('Failed to fetch comparison data:', e);
        }
    }

    function setCurrencyMode(mode) {
        if (mode === currencyMode) return;
        currencyMode = mode;
        allComparisonData = null;
        comparisonData = null;
        fetchComparisonData();
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  8. LIFECYCLE & STARTUP
    // ═══════════════════════════════════════════════════════════════════════
    //  onMount polls /health until the backend is ready, then loads data for
    //  the initial mode.  Background prefetches for other modes run in parallel.
    //  Index/symbol watchers handle subsequent navigation.

    onMount(async () => {
        lastFetchedIndex = $marketIndex;
        lastFetchedSymbol = $selectedSymbol;

        // wait for backend to finish startup before loading any data
        while (!backendReady) {
            try {
                const res = await fetchWithTimeout(`${API_BASE_URL}/health`, 5000);
                if (res.ok) {
                    const health = await res.json();
                    startupProgress = health.progress || '';
                    startupDone = health.done || [];
                    startupLoading = health.loading || [];
                    if (health.status === 'ready') {
                        backendReady = true;
                        break;
                    }
                }
            } catch {}
            await new Promise(r => setTimeout(r, 3000));
        }

        // ping backend every 4 min to prevent Cloud Run cold starts
        const keepAlive = setInterval(() => {
            fetch(`${API_BASE_URL}/health`).catch(() => {});
        }, 4 * 60 * 1000);

        if ($isOverviewMode) {
            await loadIndexOverviewData();
            await fetchComparisonData();
            isInitialLoading = false;
            // Prefetch other modes in background so switching is instant
            prefetchMacroWidgets();
            loadSummaryData($marketIndex).then(() => {
                if ($selectedSymbol) fetchStockData($selectedSymbol);
            });
            preloadAllSectorSeries();
        } else if ($isSectorMode) {
            // sector mode loads its own data via $effects — no stock/summary data needed
            isInitialLoading = false;
            // Prefetch other modes in background
            prefetchMacroWidgets();
            loadIndexOverviewData();
            fetchComparisonData();
            loadSummaryData(Object.keys(INDEX_CONFIG)[0]);
        } else if ($isMacroContextMode) {
            isInitialLoading = false;
            isIndexSwitching = false;
            // Context mode — prefetch macro widgets immediately
            prefetchMacroWidgets();
            // Background prefetch for other modes
            loadIndexOverviewData();
            fetchComparisonData();
            loadSummaryData(Object.keys(INDEX_CONFIG)[0]);
            preloadAllSectorSeries();
        } else {
            await loadSummaryData($marketIndex);
            await fetchStockData($selectedSymbol);
            isInitialLoading = false;
            // Prefetch other modes in background so switching is instant
            prefetchMacroWidgets();
            loadIndexOverviewData();
            fetchComparisonData();
            preloadAllSectorSeries();
        }

        return () => clearInterval(keepAlive);
    });

    // respond to index changes (stock mode, overview, or sectors)
    $effect(() => {
        if (!backendReady) return;
        const idx = $marketIndex;
        if (idx === lastFetchedIndex) return;
        lastFetchedIndex = idx;

        if (idx === 'macro') {
            fullStockData = [];
            displayNameText = '';
            loadIndexOverviewData();
            fetchComparisonData();
            isInitialLoading = false;
            isIndexSwitching = false;
        } else if (idx === 'sectors') {
            fullStockData = [];
            allComparisonData = null;
            comparisonData = null;
            isInitialLoading = false;
            isIndexSwitching = false;
        } else if (idx === 'context') {
            fullStockData = [];
            allComparisonData = null;
            comparisonData = null;
            isInitialLoading = false;
            isIndexSwitching = false;
            // Ensure macro widget caches are warm for context mode components
            untrack(() => prefetchMacroWidgets());
        } else {
            // Keep old chart data visible (stale-while-revalidate) — don't clear fullStockData
            allComparisonData = null;
            comparisonData = null;
            isIndexSwitching = true;
            displayNameText = '';
            lastFetchedSymbol = '';
            // load summary data for the new index (sidebar only does this via switchIndex,
            // but navigateToStock and other paths skip it)
            untrack(() => loadSummaryData(idx));
        }
    });

    // respond to symbol changes in stock mode
    $effect(() => {
        if (!backendReady) return;
        const sym = $selectedSymbol;
        if (!sym || $isOverviewMode || $isSectorMode || $isMacroContextMode) return;
        if (sym === lastFetchedSymbol) return;
        lastFetchedSymbol = sym;
        displayNameText = metadataCache[sym] || '';
        // Keep old chart data visible while new data loads — don't clear fullStockData
        selectMode = false;
        fetchStockData(sym);
    });

    // ─── Top Bar — Mode Switching ───

    let currentMode = $derived(inContext ? 'context' : inOverview ? 'macro' : inSectors ? 'sectors' : 'stocks');

    const modes = [
        { key: 'macro',   label: 'Index Benchmarks', short: 'Indices',  icon: Globe },
        { key: 'sectors', label: 'Sector Analysis',  short: 'Sectors',  icon: PieChart },
        { key: 'stocks',  label: 'Stock Browser',    short: 'Stocks',   icon: TrendingUp },
        { key: 'context', label: 'Macro Context',    short: 'Macro',    icon: Newspaper },
    ];

    function handleSwitchMode(mode) {
        if (mode === 'macro') {
            const cur = $marketIndex;
            if (cur && cur !== 'macro' && cur !== 'sectors' && cur !== 'context' && INDEX_CONFIG[cur]) {
                try { sessionStorage.setItem('dash_last_stock_index', cur); } catch {}
            }
            marketIndex.set('macro');
            loadIndexOverviewData();
        } else if (mode === 'sectors') {
            const cur = $marketIndex;
            if (cur && cur !== 'macro' && cur !== 'sectors' && cur !== 'context' && INDEX_CONFIG[cur]) {
                try { sessionStorage.setItem('dash_last_stock_index', cur); } catch {}
            }
            marketIndex.set('sectors');
        } else if (mode === 'context') {
            const cur = $marketIndex;
            if (cur && cur !== 'macro' && cur !== 'sectors' && cur !== 'context' && INDEX_CONFIG[cur]) {
                try { sessionStorage.setItem('dash_last_stock_index', cur); } catch {}
            }
            marketIndex.set('context');
        } else if (mode === 'stocks') {
            const _defaultIdx = Object.keys(INDEX_CONFIG)[0];
            const lastIdx = sessionStorage.getItem('dash_last_stock_index') || _defaultIdx;
            if (lastIdx && INDEX_CONFIG[lastIdx]) {
                marketIndex.set(lastIdx);
            } else {
                marketIndex.set(_defaultIdx);
            }
        }
    }

    // ─── Mobile Sidebar State ───
    let sidebarOpen = $state(false);

    function toggleSidebar() { sidebarOpen = !sidebarOpen; }
    function closeSidebar() { sidebarOpen = false; }
</script>

{#if backendReady}
<div class="flex flex-col h-screen w-screen bg-bg text-text overflow-hidden font-sans selection:bg-bg-active">

    <!-- ═══ TOP BAR (desktop: full tabs, mobile: branding + toggle only) ═══ -->
    <div class="shrink-0 flex flex-col bg-bg-sidebar z-50 border-b border-border">
        <div class="flex items-center h-14 px-4 max-lg:h-12 max-lg:px-3">
            <!-- branding -->
            <div class="flex items-center gap-2 shrink-0">
                <span class="inline-flex items-center justify-center w-8 h-8 rounded-md bg-accent text-white text-[11px] font-extrabold tracking-wider">GEM</span>
                <span class="text-[14px] font-semibold text-text-secondary tracking-wide whitespace-nowrap max-sm:hidden">Global Exchange Monitor</span>
            </div>

            <!-- mode tabs — desktop only -->
            <nav class="flex-1 flex items-center justify-center max-lg:hidden" aria-label="Main navigation">
                <div class="flex items-stretch h-14 gap-0.5">
                    {#each modes as mode}
                        {@const active = currentMode === mode.key}
                        {@const Icon = mode.icon}
                        <button
                            onclick={() => handleSwitchMode(mode.key)}
                            class="topnav-tab relative flex items-center gap-2.5 px-5 text-[13px] font-semibold uppercase tracking-widest
                                {active
                                    ? 'text-text topnav-active'
                                    : 'text-text-muted hover:text-text-secondary hover:bg-surface-1'}"
                        >
                            <Icon size={18} strokeWidth={active ? 2.5 : 1.8} />
                            <span>{mode.label}</span>
                            {#if active}
                                <div class="absolute bottom-0 left-3 right-3 h-[2px] bg-accent rounded-t-full"></div>
                            {/if}
                        </button>
                    {/each}
                </div>
            </nav>

            <!-- spacer on mobile to push toggle right -->
            <div class="hidden max-lg:flex flex-1"></div>

            <div class="shrink-0">
                <ThemeToggle />
            </div>
        </div>
    </div>

    <!-- ═══ BOTTOM NAV (mobile/tablet only) ═══ -->
    <nav class="hidden max-lg:flex fixed bottom-0 left-0 right-0 z-50 bg-bg-sidebar border-t border-border" style="padding-bottom: env(safe-area-inset-bottom, 0px);" aria-label="Main navigation">
        <div class="flex items-center justify-around w-full h-14">
            {#each modes as mode}
                {@const active = currentMode === mode.key}
                {@const Icon = mode.icon}
                <button
                    onclick={() => handleSwitchMode(mode.key)}
                    class="flex flex-col items-center justify-center gap-0.5 flex-1 h-full relative
                        {active ? 'text-accent' : 'text-text-muted'}"
                >
                    {#if active}
                        <div class="absolute top-0 left-3 right-3 h-[2px] bg-accent rounded-b-full"></div>
                    {/if}
                    <Icon size={20} strokeWidth={active ? 2.5 : 1.8} />
                    <span class="text-[10px] font-semibold tracking-wide uppercase">{mode.short}</span>
                </button>
            {/each}
        </div>
    </nav>

    <!-- ═══ BODY: sidebar + main ═══ -->
    <div class="flex flex-1 min-h-0 overflow-hidden max-lg:flex-col max-lg:overflow-auto">

        <!-- mobile hamburger button (hidden in context mode — no sidebar) -->
        {#if !inContext}
        <button
            onclick={toggleSidebar}
            class="hidden max-lg:flex fixed top-[52px] left-3 z-[60] items-center justify-center w-10 h-10 rounded-lg bg-bg-card border border-border shadow-sm"
            aria-label="Toggle sidebar"
        >
            <svg aria-hidden="true" class="w-5 h-5 text-text-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                {#if sidebarOpen}
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                {:else}
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
                {/if}
            </svg>
        </button>
        {/if}

        <!-- sidebar backdrop (mobile) -->
        {#if sidebarOpen}
            <button
                onclick={closeSidebar}
                class="hidden max-lg:block fixed inset-0 z-[39] bg-black/20"
                aria-label="Close sidebar"
            ></button>
        {/if}

        {#if !inContext}
        <div class="w-[460px] min-w-[300px] h-full shrink z-[40] flex flex-col
            max-lg:fixed max-lg:top-12 max-lg:bottom-14 max-lg:left-0 max-lg:w-[min(400px,85vw)] max-lg:transition-transform max-lg:duration-300 max-lg:shadow-lg
            {sidebarOpen ? 'max-lg:translate-x-0' : 'max-lg:-translate-x-full'}">
            <div class="flex-1 min-h-0 overflow-hidden">
                <Sidebar />
            </div>
        </div>
        {/if}

        <main class="flex-1 flex flex-col p-6 gap-6 overflow-hidden relative min-w-0
            max-lg:h-auto max-lg:min-h-dvh max-lg:overflow-visible max-lg:p-4 max-lg:pt-14 max-lg:pb-20 max-lg:gap-4
            max-sm:p-3 max-sm:pt-12 max-sm:pb-18 max-sm:gap-3"
            style="">

        <!-- header: title + period controls (hidden in context mode) -->
        <header class="flex shrink-0 justify-between items-center z-10 max-lg:flex-col max-lg:items-start max-lg:gap-3" class:hidden={inContext}>
            <div>
                {#if inOverview}
                    {#if highlightKey}
                        {@const hCfg = INDEX_CONFIG[highlightKey]}
                        <div class="flex items-center gap-4">
                            <span aria-hidden="true" class="{hCfg?.flag || ''} fis rounded-sm" style="font-size: 2rem;"></span>
                            <div>
                                <h1 class="text-xl max-lg:text-lg max-sm:text-base font-semibold text-text uppercase tracking-tight leading-none">{hCfg?.shortLabel || highlightKey}</h1>
                                <span class="text-[13px] font-medium uppercase tracking-[0.15em] pl-0.5 text-text-muted inline-flex items-center gap-1.5">
                                    {currencyMode === 'usd' ? 'USD-adjusted' : 'Normalized'} % change
                                    <Tooltip text="Price return only (excludes dividends). {currencyMode === 'usd' ? 'Prices converted to USD at historical ECB daily rates. ' : 'Returns in local currency. '}Indices have different sector compositions." position="bottom">
                                        <Info size={13} class="text-text-disabled hover:text-text-muted cursor-help transition-colors" />
                                    </Tooltip>
                                </span>
                            </div>
                        </div>
                    {:else}
                        <div>
                            <h1 class="text-xl max-lg:text-lg max-sm:text-base font-semibold text-text uppercase tracking-tight leading-none">Index Benchmarks</h1>
                            <span class="text-[13px] font-medium uppercase tracking-[0.15em] pl-0.5 text-text-muted inline-flex items-center gap-1.5">
                                <span class="text-text-secondary">{Object.keys(INDEX_CONFIG).length} indices</span> · {currencyMode === 'usd' ? 'USD-adjusted' : 'Local currency'} % change
                                <Tooltip text="Price return only (excludes dividends). {currencyMode === 'usd' ? 'Prices converted to USD at historical ECB daily rates. ' : 'Returns in local currency. '}Indices have different sector compositions." position="bottom">
                                    <Info size={13} class="text-text-disabled hover:text-text-muted cursor-help transition-colors" />
                                </Tooltip>
                            </span>
                        </div>
                    {/if}
                {:else if inSectors}
                    {#if $sectorAnalysisMode === 'single-index'}
                        {@const idxKey = $singleSelectedIndex[0] || 'sp500'}
                        {@const idxCfg = INDEX_CONFIG[idxKey]}
                        <div class="flex items-center gap-4">
                            <span aria-hidden="true" class="{idxCfg?.flag || ''} fis rounded-sm" style="font-size: 2rem;"></span>
                            <div>
                                <h1 class="text-xl max-lg:text-lg max-sm:text-base font-semibold text-text uppercase tracking-tight leading-none">{idxCfg?.shortLabel || idxKey}</h1>
                                <span class="text-[13px] font-medium uppercase tracking-[0.15em] pl-0.5 text-text-muted">
                                    <span class="text-text-secondary">{$selectedSectors.length < ALL_SECTORS.length ? `${$selectedSectors.length} of ${ALL_SECTORS.length}` : ALL_SECTORS.length} sectors</span> · Normalized % change
                                </span>
                            </div>
                        </div>
                    {:else}
                        <div>
                            <h1 class="text-xl max-lg:text-lg max-sm:text-base font-semibold uppercase tracking-tight leading-none" style="color: {$selectedSector ? getSectorColor($selectedSector) : 'var(--text-primary)'}">{$selectedSector || 'Sector Analysis'}</h1>
                            <span class="text-[13px] font-medium uppercase tracking-[0.15em] pl-0.5 text-text-muted">
                                <span class="text-text-secondary">{$sectorSelectedIndices.length} indices</span>
                                {#if $selectedIndustries.length > 0}
                                    {@const totalInds = (() => { const sc = industrySeriesCache[$selectedSector]; if (!sc) return 0; const s = new Set(); for (const idx of Object.values(sc)) for (const k of Object.keys(idx)) s.add(k); return s.size; })()}
                                    · <span class="text-blue-400">{$selectedIndustries.length}{totalInds ? ` of ${totalInds}` : ''} industries</span>
                                {:else}
                                    {@const totalInds = (() => { const sc = industrySeriesCache[$selectedSector]; if (!sc) return 0; const s = new Set(); for (const idx of Object.values(sc)) for (const k of Object.keys(idx)) s.add(k); return s.size; })()}
                                    {#if totalInds > 0}
                                        · <span class="text-text-secondary">{totalInds} industries</span>
                                    {/if}
                                {/if}
                                · Normalized % change
                            </span>
                        </div>
                    {/if}
                {:else}
                    <h1 class="text-2xl max-lg:text-xl max-sm:text-lg font-semibold uppercase tracking-tight leading-none" style="color: {stockSectorName ? getSectorColor(stockSectorName) : 'var(--text-primary)'}">{currentSymbol}</h1>
                    <div class="flex flex-col pl-0.5 mt-0.5">
                        <span class="text-[14px] font-medium uppercase tracking-[0.15em] {displayName ? 'text-text-muted' : 'text-text-faint'}">
                            {displayName || 'Loading...'}
                        </span>
                        {#if stockSectorIndustry}
                            <span class="text-[12px] font-medium uppercase tracking-widest" style="color: {stockSectorName ? getSectorColor(stockSectorName) : 'var(--text-secondary)'}">{stockSectorIndustry}</span>
                        {/if}
                    </div>
                {/if}
            </div>

            {#if !inContext}
            <div class="flex flex-col items-end gap-1.5 max-lg:w-full max-lg:items-start">
                <div class="flex items-center gap-2">
                    <div class="flex items-center gap-1 border border-border p-1 rounded-lg max-sm:hidden">
                        <button
                            onclick={toggleCustomMode}
                            aria-label={selectMode ? 'Exit custom date range selection' : 'Select custom date range'}
                            class="px-3 py-1.5 text-[11px] font-semibold rounded-md transition-all
                            {selectMode
                                ? 'bg-accent text-white'
                                : customRange
                                    ? 'bg-bg-selected text-text'
                                    : 'text-text-muted hover:bg-bg-hover hover:text-text'}"
                        >
                            {selectMode ? 'SELECTING...' : 'CUSTOM'}
                        </button>
                        {#if customRange}
                            <span class="text-[11px] font-medium text-text-secondary tabular-nums whitespace-nowrap pr-1">
                                {fmtDate(customRange.start)} → {fmtDate(customRange.end)}
                            </span>
                        {/if}
                    </div>

                    {#if inOverview}
                        <div class="flex border border-border p-1 rounded-lg">
                            <button
                                onclick={() => setCurrencyMode('local')}
                                aria-label="Show local currency returns"
                                aria-pressed={currencyMode === 'local'}
                                class="px-3 py-1.5 text-[11px] font-semibold rounded-md transition-all whitespace-nowrap
                                {currencyMode === 'local'
                                    ? 'bg-accent text-white'
                                    : 'text-text-muted hover:bg-bg-hover hover:text-text'}"
                            >LOCAL</button>
                            <button
                                onclick={() => setCurrencyMode('usd')}
                                aria-label="Show USD-adjusted returns"
                                aria-pressed={currencyMode === 'usd'}
                                class="px-3 py-1.5 text-[11px] font-semibold rounded-md transition-all whitespace-nowrap
                                {currencyMode === 'usd'
                                    ? 'bg-accent text-white'
                                    : fxError
                                        ? 'text-text-disabled cursor-not-allowed'
                                        : 'text-text-muted hover:bg-bg-hover hover:text-text'}"
                            >USD</button>
                        </div>
                    {/if}

                    <div class="flex border border-border p-1 rounded-lg">
                        {#each ['1W', '1MO', '3MO', '6MO', '1Y', '5Y', 'MAX'] as p}
                            <button
                                onclick={() => setPeriod(p.toLowerCase())}
                                aria-label="Set period to {p}"
                                aria-pressed={currentPeriod === p.toLowerCase()}
                                class="px-3 max-sm:px-1.5 py-1.5 text-[11px] max-sm:text-[10px] font-semibold rounded-md transition-all whitespace-nowrap
                                {currentPeriod === p.toLowerCase()
                                    ? 'bg-accent text-white'
                                    : 'text-text-muted hover:bg-bg-hover hover:text-text'}"
                            >{p}</button>
                        {/each}
                    </div>
                </div>
            </div>
            {/if}
        </header>

        <!-- main content: layout varies by mode -->
        {#if inOverview}
            <!-- OVERVIEW: single-column layout — chart + table + correlation -->
            <div class="flex-1 flex flex-col gap-4 min-h-0 min-w-0 z-0
                max-xl:flex-none max-xl:gap-4 max-xl:overflow-y-auto max-xl:overflow-x-hidden max-lg:pb-20 max-sm:pb-18">
                <Card fill padding={false} class="flex-1 w-full min-w-0 min-h-0 relative overflow-hidden
                    max-xl:flex-none max-xl:h-[50vh] max-sm:h-[40vh]
                    {selectMode ? 'ring-2 ring-text-faint/20' : ''}">
                    {#if comparisonData && comparisonData.series && comparisonData.series.length > 0}
                        <div class="flex-1 min-h-0 min-w-0">
                            <PriceChart
                                data={[]}
                                {currentPeriod}
                                {selectMode}
                                {customRange}
                                currency="%"
                                compareData={comparisonData}
                                hideVolume={true}
                                highlightSymbol={highlightTicker}
                                onResetPeriod={handleResetPeriod}
                                onRangeSelect={handleRangeSelect}
                                currencyLabel={currencyMode === 'usd' ? '$' : ''}
                            />
                        </div>
                    {:else}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <div class="flex flex-col items-center space-y-3">
                                <div class="w-6 h-6 border-2 border-border border-t-text-muted rounded-full animate-spin" aria-hidden="true"></div>
                                <span class="text-[11px] font-medium uppercase tracking-widest text-text-muted">Loading Comparison</span>
                            </div>
                        </div>
                    {/if}
                </Card>
                <div class="flex-1 min-h-0 grid grid-cols-2 gap-4 max-xl:grid-cols-1 max-xl:flex-none max-xl:auto-rows-[minmax(280px,1fr)]">
                    <div class="min-h-0 max-xl:min-h-[280px] card-enter">
                        <IndexPerformanceTable {currentPeriod} {customRange} highlightSymbol={highlightTicker} highlightPair={$macroHighlightPair} onRowClick={(key) => {
                            macroHighlightPair.set(null);
                            macroHighlightIndex.set(highlightKey === key ? null : key);
                        }} />
                    </div>
                    <div class="min-h-0 max-xl:min-h-[280px] card-enter">
                        <CorrelationHeatmap {currentPeriod} {customRange} highlightPair={$macroHighlightPair} highlightIndex={highlightKey} onCellClick={(cell) => {
                            if ($macroHighlightPair && $macroHighlightPair.row === cell.row && $macroHighlightPair.col === cell.col) {
                                macroHighlightPair.set(null);
                            } else {
                                macroHighlightIndex.set(null);
                                macroHighlightPair.set(cell);
                            }
                        }} />
                    </div>
                </div>
            </div>

        {:else if !inContext}
        <div class="{inSectors ? 'gap-4' : 'gap-5'} flex-1 flex flex-col min-h-0 min-w-0 z-0 max-lg:gap-4 max-lg:min-h-0 max-lg:flex-none max-lg:pb-20 max-sm:pb-18">
            {#if !inSectors}
                <StockMetrics />
            {/if}
            <Card fill padding={false} class="{inSectors ? 'flex-1' : 'flex-[2]'} w-full min-w-0 relative overflow-hidden
                max-lg:flex-none max-lg:h-[50vh] max-sm:h-[40vh]
                {selectMode ? 'ring-2 ring-text-faint/20' : ''}">
                {#if inSectors}
                    {#if sectorComparisonData && sectorComparisonData.series && sectorComparisonData.series.length > 0}
                        <div class="flex-1 min-h-0 min-w-0">
                            <PriceChart
                                data={[]}
                                {currentPeriod}
                                {selectMode}
                                {customRange}
                                currency="%"
                                compareData={sectorComparisonData}
                                compareColors={sectorCompareColors}
                                compareNames={sectorCompareNames}
                                hideVolume={true}
                                hideLegend={$sectorAnalysisMode === 'single-index'}
                                highlightSymbol={$sectorAnalysisMode === 'single-index' && $sectorHighlightEnabled ? $selectedSector : null}
                                onResetPeriod={handleResetPeriod}
                                onRangeSelect={handleRangeSelect}
                            />
                        </div>
                    {:else}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <div class="flex flex-col items-center space-y-3">
                                {#if sectorDataLoading || !allSectorSeries}
                                    <div class="w-6 h-6 border-2 border-border border-t-text-muted rounded-full animate-spin" aria-hidden="true"></div>
                                    <span class="text-[11px] font-medium uppercase tracking-widest text-text-muted">Loading Sector Data</span>
                                {:else}
                                    <span class="text-[11px] font-medium uppercase tracking-widest text-text-muted">Select a sector</span>
                                {/if}
                            </div>
                        </div>
                    {/if}
                {:else}
                    {#if (isInitialLoading || isIndexSwitching) && fullStockData.length === 0}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <div class="flex flex-col items-center space-y-3">
                                <div class="w-6 h-6 border-2 border-border border-t-text-muted rounded-full animate-spin" aria-hidden="true"></div>
                                <span class="text-[11px] font-medium uppercase tracking-widest text-text-muted">Loading Chart</span>
                            </div>
                        </div>
                    {:else if fullStockData.length === 0}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <span class="text-[11px] font-medium uppercase tracking-widest text-text-muted">No chart data available</span>
                        </div>
                    {/if}
                    <div class="flex-1 min-h-0 min-w-0 chart-no-animate" style="transition: none !important;">
                        <PriceChart
                            data={fullStockData}
                            {currentPeriod}
                            {selectMode}
                            {customRange}
                            currency={ccy}
                            onResetPeriod={handleResetPeriod}
                            onRangeSelect={handleRangeSelect}
                        />
                    </div>
                {/if}
            </Card>

            <!-- bottom panels: vary by mode -->
            {#if inSectors}
                {#if $sectorAnalysisMode === 'single-index'}
                    <div class="flex-1 grid grid-cols-2 gap-4 min-h-0
                        max-lg:flex-none max-lg:grid-cols-1 max-lg:auto-rows-[minmax(280px,1fr)]">
                        <div class="min-h-0 max-lg:min-h-[280px] card-enter">
                            <SectorRankings {currentPeriod} {customRange} industryReturnOverride={rankingsIndustryOverride} />
                        </div>
                        <div class="min-h-0 max-lg:min-h-[350px] card-enter">
                            <IndustryBreakdown {currentPeriod} {customRange} />
                        </div>
                    </div>
                {:else}
                    <div class="flex-1 grid grid-cols-12 gap-4 min-h-0 overflow-hidden
                        max-xl:grid-cols-1 max-xl:flex-none max-xl:auto-rows-auto max-xl:overflow-visible">
                        <div class="col-span-8 min-h-0 max-xl:col-span-1 max-xl:min-h-[320px] card-enter">
                            <SectorHeatmap {currentPeriod} {customRange} industryFilter={$selectedIndustries.length > 0 ? $selectedIndustries : null} filteredSector={$selectedSector} {allSectorSeries} {industrySeriesCache} />
                        </div>
                        <div class="col-span-4 min-h-0 max-xl:col-span-1 max-xl:min-h-[300px] card-enter">
                            <SectorTopStocks {currentPeriod} {customRange} industryFilter={$selectedIndustries.length > 0 ? $selectedIndustries : null} {topStocksCache} />
                        </div>
                    </div>
                {/if}
            {:else}
                <div class="flex-[1.2] grid grid-cols-3 gap-4 min-h-0
                    max-xl:grid-cols-2
                    max-lg:grid-cols-1 max-lg:flex-none max-lg:auto-rows-[minmax(200px,1fr)]">
                    <div class="min-h-0 max-lg:min-h-[200px] card-enter">
                        <MostActive {currentPeriod} {customRange} />
                    </div>
                    <div class="min-h-0 flex flex-col max-lg:min-h-[250px] card-enter">
                        <TopMovers {currentPeriod} {customRange} />
                    </div>
                    <div class="min-h-0 max-lg:min-h-[250px] card-enter overflow-visible">
                        <TechnicalLevels {currentPeriod} {customRange} />
                    </div>
                </div>
            {/if}
        </div>
        {/if}

        <!-- MACRO CONTEXT: persisted after first visit to avoid destroy/recreate delay -->
        {#if contextEverVisited}
            <div class="flex-1 flex flex-col gap-4 min-h-0 min-w-0 z-0
                max-xl:flex-none max-xl:gap-4 max-xl:overflow-y-auto max-xl:overflow-x-hidden max-lg:pb-20 max-sm:pb-18"
                class:hidden={!inContext}>
                <!-- Risk dashboard strip -->
                <Card fill={false} padding={false} class="shrink-0 !overflow-visible">
                    <div class="px-4 py-3">
                        <RiskDashboard />
                    </div>
                </Card>
                <!-- BTC / Gold / EUR-USD ticker strip -->
                <Card fill={false} padding={false} class="shrink-0">
                    <div class="flex items-stretch gap-4 px-4 py-2.5 max-sm:flex-col max-sm:gap-2">
                        {#each ['BINANCE:BTCUSDT', 'FXCM:XAU/USD', 'FXCM:EUR/USD'] as symbol}
                            {@const data = macroTickerQuotes[symbol] || { price: 0, pct: 0, diff: 0 }}
                            {@const hasData = data.price > 0}
                            {@const sym = symbol.includes(':') ? symbol.split(':')[1] : symbol}
                            {@const ccy = symbol.includes('EUR/USD') ? '' : '$'}
                            {@const status = macroTickerStatus(symbol)}
                            <div class="flex items-center gap-3 flex-1 min-w-0">
                                <div class="flex flex-col shrink-0">
                                    <span class="text-[13px] font-semibold text-text uppercase tracking-tighter">{sym}</span>
                                    {#if status.label}
                                        <span class="flex items-center gap-1 mt-0.5">
                                            <span class="w-1.5 h-1.5 rounded-full {status.dot} {status.pulse ? 'animate-pulse' : ''}"></span>
                                            <span class="text-[9px] font-bold {status.color} uppercase tracking-tighter">{status.label}</span>
                                        </span>
                                    {/if}
                                </div>
                                {#if hasData}
                                    <span class="text-[13px] font-mono font-bold text-text-secondary tabular-nums">
                                        <span class="text-text-muted">{ccy}</span>{data.price.toLocaleString(undefined, {
                                            minimumFractionDigits: sym.includes('EUR/USD') ? 4 : 2,
                                            maximumFractionDigits: sym.includes('EUR/USD') ? 4 : 2,
                                        })}
                                    </span>
                                    <span class="text-[12px] font-mono font-bold tabular-nums {(data.pct ?? 0) >= 0 ? 'text-up' : 'text-down'}">
                                        {(data.pct ?? 0) >= 0 ? '+' : ''}{(data.pct ?? 0).toFixed(2)}%
                                    </span>
                                {:else}
                                    <span class="text-[12px] text-text-muted animate-pulse">---</span>
                                {/if}
                            </div>
                        {/each}
                    </div>
                </Card>
                <!-- 3-column grid: watchlist, news, calendar -->
                <div class="flex-1 min-h-0 grid grid-cols-3 gap-4
                    max-xl:grid-cols-1 max-xl:flex-none">
                    <div class="min-h-0 overflow-hidden max-xl:h-[70vh]">
                        <MacroWatchlist />
                    </div>
                    <div class="min-h-0 overflow-hidden max-xl:h-[70vh]">
                        <NewsFeed />
                    </div>
                    <div class="min-h-0 overflow-hidden max-xl:h-[70vh]">
                        <EconomicCalendar {currentPeriod} {customRange} />
                    </div>
                </div>
            </div>
        {/if}

    </main>
    </div>
</div>
{:else}
<!-- startup loading screen — shown while backend is warming up -->
<div class="flex h-dvh w-screen bg-bg text-text items-center justify-center font-sans">
    <div class="flex flex-col items-center gap-6 max-w-md w-full px-8">
        <div class="w-8 h-8 border-2 border-border border-t-accent rounded-full animate-spin" role="status" aria-label="Loading"></div>
        <div class="text-center">
            <h2 class="text-lg font-semibold text-text uppercase tracking-widest mb-1">Loading Data</h2>
            <p aria-live="polite" class="text-xs text-text-muted font-medium uppercase tracking-wider">{startupProgress || 'Connecting...'}</p>
        </div>
        {#if startupDone.length > 0}
            {@const parts = startupProgress.split('/')}
            {@const pctDone = parts.length === 2 ? (Number(parts[0]) / Number(parts[1]) * 100) : 0}
            <div class="w-full">
                <div class="w-full bg-border-subtle rounded-full h-1.5 overflow-hidden">
                    <div class="h-full bg-accent rounded-full transition-all duration-500"
                         style="width: {pctDone}%"></div>
                </div>
                <div class="mt-4 max-h-48 overflow-y-auto space-y-1">
                    {#each startupLoading as item}
                        <div class="flex items-center gap-2 text-[11px] font-medium text-text-secondary uppercase tracking-wider">
                            <div aria-hidden="true" class="w-2 h-2 border border-text-faint border-t-text-muted rounded-full animate-spin flex-shrink-0"></div>
                            {item}
                        </div>
                    {/each}
                    {#each startupDone.slice(-6) as item}
                        <div class="flex items-center gap-2 text-[11px] font-medium text-text-faint uppercase tracking-wider">
                            <div aria-hidden="true" class="w-2 h-2 rounded-full bg-up/40 flex-shrink-0"></div>
                            {item}
                        </div>
                    {/each}
                </div>
            </div>
        {/if}
    </div>
</div>
{/if}

<style>
    .macro-scroll::-webkit-scrollbar { width: 4px; }
    .macro-scroll::-webkit-scrollbar-track { background: transparent; }
    .macro-scroll::-webkit-scrollbar-thumb { background: var(--border-subtle); border-radius: 10px; }
    .macro-scroll::-webkit-scrollbar-thumb:hover { background: var(--border-default); }
</style>