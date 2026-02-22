<!-- main dashboard page: orchestrates stock view, overview mode, and sector comparison
     mode (cross-index and single-index sub-modes) with period/range controls -->
<script>
    import { selectedSymbol, loadSummaryData, marketIndex, currentCurrency, INDEX_CONFIG, summaryData, isOverviewMode, overviewSelectedIndices, INDEX_TICKER_MAP, loadIndexOverviewData, isSectorMode, sectorSelectedIndices, singleSelectedIndex, selectedSector, sectorAnalysisMode, selectedIndustries, crossSelectedIndustries, singleModeIndustries, selectedSectors, sectorsByIndex } from '$lib/stores.js';
    import { API_BASE_URL } from '$lib/config.js';
    import Sidebar from '$lib/components/Sidebar.svelte';
    import Chart from '$lib/components/Chart.svelte';
    import RankingPanel from '$lib/components/RankingPanel.svelte';
    import IndexPerformanceTable from '$lib/components/IndexPerformanceTable.svelte';
    import SectorHeatmap from '$lib/components/SectorHeatmap.svelte';
    import SectorTopStocks from '$lib/components/SectorTopStocks.svelte';
    import SectorRankings from '$lib/components/SectorRankings.svelte';
    import IndustryBreakdown from '$lib/components/IndustryBreakdown.svelte';
    import LiveIndicators from '$lib/components/LiveIndicators.svelte';
    import { onMount, untrack } from 'svelte';

    // --- FETCH UTILITIES ---

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

    // --- STOCK & COMPARISON STATE ---

    let fullStockData = $state([]);
    let allComparisonData = $state(null);
    let comparisonData = $state(null);

    // filter allComparisonData down to only the user-selected indices
    $effect(() => {
        const selected = $overviewSelectedIndices;
        const all = allComparisonData;
        if (!all || !all.series) {
            comparisonData = null;
            return;
        }
        const filtered = all.series.filter(s => selected.includes(s.symbol));
        if (filtered.length === 0) {
            comparisonData = null;
        } else {
            comparisonData = { series: filtered };
        }
    });
    let isInitialLoading = $state(true);
    let isIndexSwitching = $state(false);
    let inOverview = $derived($isOverviewMode);
    let inSectors = $derived($isSectorMode);

    // --- BACKEND STARTUP GATE ---
    // poll /health until backend reports ready before loading any data

    let backendReady = $state(false);
    let startupProgress = $state('');
    let startupDone = $state([]);
    let startupLoading = $state([]);

    // --- SECTOR COMPARISON STATE ---

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

    // --- SECTOR DISPLAY CONSTANTS ---

    const SECTOR_INDEX_COLORS = {
        sp500: '#e2e8f0', stoxx50: '#2563eb', ftse100: '#ec4899',
        nikkei225: '#f59e0b', csi300: '#ef4444', nifty50: '#22c55e',
    };
    const SECTOR_INDEX_NAMES = {
        sp500: 'S&P 500', stoxx50: 'STOXX 50', ftse100: 'FTSE 100',
        nikkei225: 'Nikkei 225', csi300: 'CSI 300', nifty50: 'Nifty 50',
    };

    // palette and abbreviations for single-index multi-sector chart legends
    const SECTOR_PALETTE = ['#8b5cf6', '#06b6d4', '#f59e0b', '#ef4444', '#22c55e', '#ec4899', '#3b82f6', '#f97316', '#84cc16', '#a855f7', '#14b8a6'];
    const SECTOR_ABBREV = {
        'Technology': 'Tech', 'Financial Services': 'Financials', 'Healthcare': 'Health',
        'Industrials': 'Industls', 'Consumer Cyclical': 'Cons Cyc',
        'Communication Services': 'Comms', 'Consumer Defensive': 'Cons Def',
        'Basic Materials': 'Materials', 'Real Estate': 'Real Est',
        'Energy': 'Energy', 'Utilities': 'Utilities'
    };
    const ALL_SECTORS = ['Technology', 'Financial Services', 'Healthcare', 'Industrials', 'Consumer Cyclical', 'Communication Services', 'Consumer Defensive', 'Energy', 'Basic Materials', 'Utilities', 'Real Estate'];

    function getSectorColor(sec) {
        const idx = ALL_SECTORS.indexOf(sec);
        return SECTOR_PALETTE[(idx >= 0 ? idx : ALL_SECTORS.length) % SECTOR_PALETTE.length];
    }

    // map sector/index keys to colors depending on analysis mode
    let sectorCompareColors = $derived((() => {
        if ($sectorAnalysisMode === 'single-index') {
            const colors = {};
            $selectedSectors.forEach(sec => { colors[sec] = getSectorColor(sec); });
            return colors;
        }
        return SECTOR_INDEX_COLORS;
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

    // --- SECTOR DATA LOADING ---

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

    // --- INDUSTRY SERIES COMPUTATION ---

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

    // --- SECTOR FETCH ORCHESTRATION ---

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

    // --- REACTIVE EFFECTS ---

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

    // --- PERIOD / RANGE STATE ---

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

    // --- METADATA & DISPLAY NAME ---

    let metadataCache = {};
    let metadataFetchId = 0;
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

    // --- PERIOD / RANGE HELPERS ---

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

    // --- STOCK DATA LOADING ---

    async function fetchStockData(symbol) {
        if (!symbol) return;

        // fire-and-forget metadata fetch to populate displayNameText
        const fetchId = ++metadataFetchId;
        fetchWithTimeout(`${API_BASE_URL}/metadata/${encodeURIComponent(symbol)}`, 8000)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.name && fetchId === metadataFetchId) {
                    metadataCache[symbol] = data.name;
                    displayNameText = data.name;
                }
            })
            .catch(() => {});

        // retry with backoff — backend may be lazy-loading the index on first request
        const maxRetries = 5;
        for (let attempt = 0; attempt < maxRetries; attempt++) {
            try {
                const res = await fetchWithTimeout(
                    `${API_BASE_URL}/data/${encodeURIComponent(symbol)}?period=max&t=${Date.now()}`, 30000
                );
                if (res.ok) {
                    const json = await res.json();
                    if (json && json.length > 0) {
                        fullStockData = json;
                        isInitialLoading = false;
                        isIndexSwitching = false;
                        return;
                    }
                    if (attempt < maxRetries - 1) {
                        await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
                        continue;
                    }
                }
            } catch (e) {
                if (attempt < maxRetries - 1) {
                    await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
                    continue;
                }
                console.error(`Failed to fetch data for ${symbol}:`, e);
            }
        }
        isInitialLoading = false;
        isIndexSwitching = false;
    }

    // --- INDUSTRY RETURN OVERRIDES FOR SUB-COMPONENTS ---

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

    // --- OVERVIEW DATA LOADING ---

    // fetch all 6 indices at once — client-side filtering keeps it responsive
    async function fetchComparisonData() {
        const allTickers = Object.keys(INDEX_TICKER_MAP);
        try {
            const res = await fetchWithTimeout(
                `${API_BASE_URL}/index-prices/data?symbols=${encodeURIComponent(allTickers.join(','))}&period=max&t=${Date.now()}`,
                15000,
                { headers: { 'Cache-Control': 'no-cache' } }
            );
            if (res.ok) {
                allComparisonData = await res.json();
            }
        } catch (e) {
            console.error('Failed to fetch comparison data:', e);
        }
    }

    // --- LIFECYCLE & REACTIVE INDEX/SYMBOL WATCHERS ---

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
        } else if ($isSectorMode) {
            // sector mode loads its own data via $effects — no stock/summary data needed
            isInitialLoading = false;
        } else {
            await loadSummaryData($marketIndex);
            await fetchStockData($selectedSymbol);
            isInitialLoading = false;
        }

        return () => clearInterval(keepAlive);
    });

    // respond to index changes (stock mode, overview, or sectors)
    $effect(() => {
        if (!backendReady) return;
        const idx = $marketIndex;
        if (idx === lastFetchedIndex) return;
        lastFetchedIndex = idx;

        if (idx === 'overview') {
            fullStockData = [];
            displayNameText = '';
            loadIndexOverviewData();
            fetchComparisonData();
            isInitialLoading = false;
            isIndexSwitching = false;
        } else if (idx === 'sectors') {
            // sector mode loads data via its own $effects
            fullStockData = [];
            allComparisonData = null;
            comparisonData = null;
            isInitialLoading = false;
            isIndexSwitching = false;
        } else {
            fullStockData = [];
            allComparisonData = null;
            comparisonData = null;
            isIndexSwitching = true;
            displayNameText = '';
            lastFetchedSymbol = '';
        }
    });

    // respond to symbol changes in stock mode
    $effect(() => {
        if (!backendReady) return;
        const sym = $selectedSymbol;
        if (!sym || $isOverviewMode || $isSectorMode) return;
        if (sym === lastFetchedSymbol) return;
        lastFetchedSymbol = sym;
        displayNameText = metadataCache[sym] || '';
        fullStockData = [];
        selectMode = false;
        fetchStockData(sym);
    });
    // overview index checkbox changes are handled reactively by the $effect that
    // derives comparisonData from allComparisonData + overviewSelectedIndices
</script>

{#if backendReady}
<div class="flex h-screen w-screen bg-[#0d0d12] text-[#d1d1d6] overflow-hidden font-sans selection:bg-purple-500/30">

    <div class="w-[460px] h-full shrink-0 z-20 shadow-2xl shadow-black/50">
        <Sidebar />
    </div>

    <main class="flex-1 flex flex-col p-6 gap-6 h-screen overflow-hidden relative min-w-0">

        <!-- header: title + period controls -->
        <header class="flex shrink-0 justify-between items-center z-10">
            <div>
                {#if inOverview}
                    <h1 class="text-4xl font-black text-white/85 uppercase tracking-tighter drop-shadow-lg leading-none">INDEX OVERVIEW</h1>
                    <span class="text-sm font-bold uppercase tracking-[0.2em] pl-1 text-white/30">
                        <span class="text-orange-400/70">{$overviewSelectedIndices.length} indices</span> · Normalized % change
                    </span>
                {:else if inSectors}
                    {@const sectorFlagMap = { sp500: 'fi fi-us', stoxx50: 'fi fi-eu', ftse100: 'fi fi-gb', nikkei225: 'fi fi-jp', csi300: 'fi fi-cn', nifty50: 'fi fi-in' }}
                    {#if $sectorAnalysisMode === 'single-index'}
                        {@const idxKey = $singleSelectedIndex[0] || 'sp500'}
                        {@const idxCfg = INDEX_CONFIG[idxKey]}
                        <div class="flex items-center gap-4">
                            <span class="{sectorFlagMap[idxKey] || ''} fis rounded-sm" style="font-size: 2.2rem;"></span>
                            <div>
                                <h1 class="text-4xl font-black text-white/85 uppercase tracking-tighter drop-shadow-lg leading-none">{idxCfg?.shortLabel || idxKey}</h1>
                                <span class="text-sm font-bold uppercase tracking-[0.2em] pl-0.5 text-white/30">
                                    <span class="text-orange-400/70">{$selectedSectors.length} sectors</span> · Normalized % change
                                </span>
                            </div>
                        </div>
                    {:else}
                        <div>
                            <h1 class="text-4xl font-black text-white/85 uppercase tracking-tighter drop-shadow-lg leading-none">{$selectedSector || 'SECTOR ANALYSIS'}</h1>
                            <span class="text-sm font-bold uppercase tracking-[0.2em] pl-0.5 text-white/30">
                                <span class="text-orange-400/70">{$sectorSelectedIndices.length} indices</span>
                                {#if $selectedIndustries.length > 0}
                                    {@const totalInds = (() => { const sc = industrySeriesCache[$selectedSector]; if (!sc) return 0; const s = new Set(); for (const idx of Object.values(sc)) for (const k of Object.keys(idx)) s.add(k); return s.size; })()}
                                    · <span class="text-orange-400/70">{$selectedIndustries.length}{totalInds ? ` of ${totalInds}` : ''} industries</span>
                                {:else}
                                    {@const totalInds = (() => { const sc = industrySeriesCache[$selectedSector]; if (!sc) return 0; const s = new Set(); for (const idx of Object.values(sc)) for (const k of Object.keys(idx)) s.add(k); return s.size; })()}
                                    {#if totalInds > 0}
                                        · <span class="text-orange-400/70">{totalInds} industries</span>
                                    {/if}
                                {/if}
                                · Normalized % change
                            </span>
                        </div>
                    {/if}
                {:else}
                    <h1 class="text-5xl font-black text-white/85 uppercase tracking-tighter drop-shadow-lg leading-none">{currentSymbol}</h1>
                    <span class="text-sm font-bold uppercase tracking-[0.2em] pl-1 {displayName ? 'text-white/40' : 'text-white/15'}">
                        {displayName || 'Loading...'}
                    </span>
                {/if}
            </div>

            <div class="flex items-center gap-3">
                <div class="flex items-center gap-2 bg-white/5 border border-white/10 p-1 rounded-xl shadow-2xl backdrop-blur-md">
                    <button
                        onclick={toggleCustomMode}
                        class="px-4 py-1.5 text-[10px] font-black rounded-lg transition-all duration-300
                        {selectMode
                            ? 'bg-orange-500 text-white shadow-[0_0_15px_rgba(249,115,22,0.4)] animate-pulse'
                            : customRange
                                ? 'bg-orange-500/20 text-orange-400 shadow-[0_0_10px_rgba(249,115,22,0.2)]'
                                : 'text-white/40 hover:bg-white/5 hover:text-white'}"
                    >
                        {selectMode ? '⊞ SELECTING...' : 'CUSTOM RANGE'}
                    </button>
                    {#if customRange}
                        <span class="text-[10px] font-bold text-orange-400/80 tabular-nums whitespace-nowrap pr-2">
                            {fmtDate(customRange.start)} → {fmtDate(customRange.end)}
                        </span>
                    {/if}
                </div>

                <div class="flex bg-white/5 border border-white/10 p-1 rounded-xl shadow-2xl backdrop-blur-md">
                    {#each ['1W', '1MO', '3MO', '6MO', '1Y', '5Y', 'MAX'] as p}
                        <button
                            onclick={() => setPeriod(p.toLowerCase())}
                            class="px-4 py-1.5 text-[10px] font-black rounded-lg transition-all duration-300
                            {currentPeriod === p.toLowerCase()
                                ? 'bg-purple-500 text-white shadow-[0_0_15px_rgba(168,85,247,0.4)] scale-105'
                                : 'text-white/40 hover:bg-white/5 hover:text-white'}"
                        >{p}</button>
                    {/each}
                </div>
            </div>
        </header>

        <!-- main content: chart + bottom panels, layout varies by mode -->
        <div class="{inSectors ? 'gap-4' : 'gap-6'} flex-1 flex flex-col min-h-0 min-w-0 z-0">
            <section class="{inSectors ? 'flex-[9]' : 'flex-[2]'} w-full min-w-0 bg-[#111114] rounded-3xl border border-white/5 relative overflow-hidden flex flex-col shadow-2xl
                {selectMode ? 'ring-2 ring-orange-500/30' : ''}" >
                <div class="absolute inset-0 bg-gradient-to-b from-white/5 to-transparent pointer-events-none opacity-50"></div>
                {#if inOverview}
                    {#if comparisonData && comparisonData.series && comparisonData.series.length > 0}
                        <div class="flex-1 min-h-0 min-w-0">
                            <Chart
                                data={[]}
                                {currentPeriod}
                                {selectMode}
                                {customRange}
                                currency="%"
                                compareData={comparisonData}
                                onResetPeriod={handleResetPeriod}
                                onRangeSelect={handleRangeSelect}
                            />
                        </div>
                    {:else}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <div class="flex flex-col items-center space-y-3 opacity-30">
                                <div class="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                                <span class="text-[10px] font-black uppercase tracking-widest text-white">Loading Comparison</span>
                            </div>
                        </div>
                    {/if}
                {:else if inSectors}
                    {#if sectorComparisonData && sectorComparisonData.series && sectorComparisonData.series.length > 0}
                        <div class="flex-1 min-h-0 min-w-0">
                            <Chart
                                data={[]}
                                {currentPeriod}
                                {selectMode}
                                {customRange}
                                currency="%"
                                compareData={sectorComparisonData}
                                compareColors={sectorCompareColors}
                                compareNames={sectorCompareNames}
                                hideVolume={true}
                                onResetPeriod={handleResetPeriod}
                                onRangeSelect={handleRangeSelect}
                            />
                        </div>
                    {:else if sectorComparisonData?._empty}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <div class="flex flex-col items-center space-y-3 opacity-30">
                                <span class="text-[11px] font-black uppercase tracking-widest text-white/40">No Data</span>
                            </div>
                        </div>
                    {:else}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <div class="flex flex-col items-center space-y-3 opacity-30">
                                {#if sectorDataLoading}
                                    <div class="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                                {/if}
                                <span class="text-[10px] font-black uppercase tracking-widest text-white">{sectorDataLoading ? 'Loading Sector Data' : 'Select a sector'}</span>
                            </div>
                        </div>
                    {/if}
                {:else}
                    {#if (isInitialLoading || isIndexSwitching) && fullStockData.length === 0}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <div class="flex flex-col items-center space-y-3 opacity-30">
                                <div class="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                                <span class="text-[10px] font-black uppercase tracking-widest text-white">Loading Chart</span>
                            </div>
                        </div>
                    {:else if fullStockData.length === 0}
                        <div class="absolute inset-0 flex items-center justify-center z-10">
                            <div class="flex flex-col items-center space-y-3 opacity-30">
                                <div class="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                                <span class="text-[10px] font-black uppercase tracking-widest text-white">Loading Data</span>
                            </div>
                        </div>
                    {/if}
                    <div class="flex-1 min-h-0 min-w-0 chart-no-animate" style="transition: none !important;">
                        <Chart
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
            </section>

            <!-- bottom panels: vary by mode -->
            {#if inOverview}
                <div class="flex-1 min-h-0">
                    <IndexPerformanceTable {currentPeriod} {customRange} />
                </div>
            {:else if inSectors}
                {#if $sectorAnalysisMode === 'single-index'}
                    <div class="flex-[11] grid grid-cols-2 gap-4 min-h-0">
                        <div class="min-h-0">
                            <SectorRankings {currentPeriod} {customRange} industryReturnOverride={rankingsIndustryOverride} />
                        </div>
                        <div class="min-h-0">
                            <IndustryBreakdown {currentPeriod} {customRange} />
                        </div>
                    </div>
                {:else}
                    <div class="flex-[11] grid grid-cols-12 gap-4 min-h-0 overflow-hidden">
                        <div class="col-span-8 min-h-0">
                            <SectorHeatmap {currentPeriod} {customRange} industryFilter={$selectedIndustries.length > 0 ? $selectedIndustries : null} filteredSector={$selectedSector} {allSectorSeries} {industrySeriesCache} />
                        </div>
                        <div class="col-span-4 min-h-0">
                            <SectorTopStocks {currentPeriod} {customRange} industryFilter={$selectedIndustries.length > 0 ? $selectedIndustries : null} {topStocksCache} />
                        </div>
                    </div>
                {/if}
            {:else}
                <div class="flex-1 grid grid-cols-12 gap-6 min-h-0">
                    <div class="col-span-4 min-h-0 flex flex-col">
                        <RankingPanel {currentPeriod} {customRange} />
                    </div>
                    <div class="col-span-4 min-h-0">
                        <LiveIndicators title="MARKET LEADERS" symbols={['NVDA', 'AAPL', 'MSFT']} dynamicByIndex={true} />
                    </div>
                    <div class="col-span-4 min-h-0">
                        <LiveIndicators title="GLOBAL MACRO" subtitle=" " symbols={['BINANCE:BTCUSDT', 'FXCM:XAU/USD', 'FXCM:EUR/USD']} />
                    </div>
                </div>
            {/if}
        </div>
    </main>
</div>
{:else}
<!-- startup loading screen — shown while backend is warming up -->
<div class="flex h-screen w-screen bg-[#0d0d12] text-[#d1d1d6] items-center justify-center font-sans">
    <div class="flex flex-col items-center gap-6 max-w-md w-full px-8">
        <div class="w-10 h-10 border-2 border-white/10 border-t-purple-500 rounded-full animate-spin"></div>
        <div class="text-center">
            <h2 class="text-lg font-black text-white/70 uppercase tracking-widest mb-1">Loading Data</h2>
            <p class="text-xs text-white/30 font-bold uppercase tracking-wider">{startupProgress || 'Connecting...'}</p>
        </div>
        {#if startupDone.length > 0}
            {@const parts = startupProgress.split('/')}
            {@const pctDone = parts.length === 2 ? (Number(parts[0]) / Number(parts[1]) * 100) : 0}
            <div class="w-full">
                <div class="w-full bg-white/5 rounded-full h-1.5 overflow-hidden">
                    <div class="h-full bg-purple-500 rounded-full transition-all duration-500"
                         style="width: {pctDone}%"></div>
                </div>
                <div class="mt-4 max-h-48 overflow-y-auto space-y-1">
                    {#each startupLoading as item}
                        <div class="flex items-center gap-2 text-[10px] font-bold text-orange-400/70 uppercase tracking-wider">
                            <div class="w-2 h-2 border border-orange-400/50 border-t-orange-400 rounded-full animate-spin flex-shrink-0"></div>
                            {item}
                        </div>
                    {/each}
                    {#each startupDone.slice(-6) as item}
                        <div class="flex items-center gap-2 text-[10px] font-bold text-white/20 uppercase tracking-wider">
                            <div class="w-2 h-2 rounded-full bg-green-500/40 flex-shrink-0"></div>
                            {item}
                        </div>
                    {/each}
                </div>
            </div>
        {/if}
    </div>
</div>
{/if}