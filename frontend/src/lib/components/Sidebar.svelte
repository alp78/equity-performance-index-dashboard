<!-- collapsible stock list grouped by sector. three modes: stock browsing,
     cross-index sector tree with industry checkboxes, single-index sector
     tree with per-sector industry filters. -->

<script>
    import { onMount, tick, untrack } from 'svelte';
    import { selectedSymbol, summaryData, loadSummaryData, marketIndex, currentCurrency, INDEX_CONFIG, INDEX_GROUPS, focusSymbolRequest, isOverviewMode, overviewSelectedIndices, loadIndexOverviewData, indexOverviewData, INDEX_TICKER_MAP, INDEX_KEY_TO_TICKER, isSectorMode, sectorSelectedIndices, singleSelectedIndex, selectedSector, sectorAnalysisMode, selectedIndustries, crossSelectedIndustries, singleModeIndustries, selectedSectors } from '$lib/stores.js';

    // --- UI STATE ---

    let searchQuery = $state('');
    let dropdownOpen = $state(false);
    let scrollContainer;
    let openSectors = $state(new Set());

    // --- DERIVED STATE ---

    let tickers = $derived($summaryData.assets || []);
    let loading = $derived($summaryData.loading);
    let error = $derived($summaryData.error);
    let currentIndex = $derived($marketIndex);
    let ccy = $derived($currentCurrency);
    let currentConfig = $derived($isOverviewMode ? { shortLabel: 'INDEX OVERVIEW', currencyCode: '%', currency: '' } : $isSectorMode ? { shortLabel: 'SECTOR ANALYSIS', currencyCode: '%', currency: '' } : INDEX_CONFIG[currentIndex]);
    let inOverview = $derived($isOverviewMode);
    let inSectors = $derived($isSectorMode);

    // --- CONSTANTS ---

    const INDEX_FLAGS = {
        stoxx50:   'fi fi-eu',
        sp500:     'fi fi-us',
        ftse100:   'fi fi-gb',
        nikkei225: 'fi fi-jp',
        csi300:    'fi fi-cn',
        nifty50:   'fi fi-in',
    };

    const INDEX_COLORS = {
        '^GSPC':     '#e2e8f0',
        '^STOXX50E': '#2563eb',
        '^FTSE':     '#ec4899',
        '^N225':     '#f59e0b',
        '000300.SS': '#ef4444',
        '^NSEI':     '#22c55e',
    };

    // --- OVERVIEW MODE ---

    let overviewAssets = $derived($indexOverviewData.assets || []);

    function toggleOverviewIndex(ticker) {
        overviewSelectedIndices.update(list => {
            if (list.includes(ticker)) {
                return list.length > 1 ? list.filter(t => t !== ticker) : list; // keep at least 1
            }
            return [...list, ticker];
        });
    }

    function switchToOverview() {
        marketIndex.set('overview');
        dropdownOpen = false;
        searchQuery = '';
        openSectors = new Set();
        loadIndexOverviewData();
    }

    function switchToSectors() {
        marketIndex.set('sectors');
        dropdownOpen = false;
        searchQuery = '';
        openSectors = new Set();
    }

    // --- SECTOR ANALYSIS STATE ---

    let availableSectors = $state([]);
    let sectorsLoaded = $state(false);
    let sectorIndustries = $state([]);
    let _lastLoadedSector = '';
    // tracks last sector+mode the effect reacted to, so it only runs on actual changes
    let _lastEffectSector = '';
    let _lastEffectMode = '';
    // per-mode selected sector so switching modes doesn't overwrite the other mode's selection
    let _crossSector = $selectedSector || 'Basic Materials';
    let _singleSector = $selectedSector || 'Basic Materials';
    // true when user has never explicitly navigated to sector mode this session
    let _sectorDefaultNeeded = !sessionStorage.getItem('dash_sector_visited');
    let sectorPanelOpen = $state(new Set());
    // cache: { compositeKey: [{industry, total}] }
    let allSectorIndustries = $state({});
    // { cacheKey: [{industry, total}] } for single-index expanded sectors
    let singleIndexIndustries = $state({});
    // persisted: which index is expanded in single-index mode
    let singleOpenIndex = $state((() => {
        try { return sessionStorage.getItem('dash_single_open_index') || 'stoxx50'; } catch { return 'stoxx50'; }
    })());
    let singleOpenSectors = $state(new Set());
    // { indexKey: [sectorName, ...] } per-index sector list
    let singleIndexSectors = $state({});
    // { sectorName: [industryName, ...] } per-sector industry filter (empty = all selected)
    let singleSelectedIndustries = $state({});

    // --- SECTOR ANALYSIS: INDEX TOGGLE ---

    function toggleSectorIndex(key) {
        sectorSelectedIndices.update(list => {
            if (list.includes(key)) {
                return list.length > 1 ? list.filter(k => k !== key) : list;
            }
            return [...list, key];
        });
        // reload industries for the currently open sector with the new index combination
        if ($selectedSector) {
            loadIndustries($selectedSector);
        }
    }

    // --- STOCK COUNT HELPERS ---

    // total stocks for an index, filtered by selected sectors and per-sector industry filters
    function getIndexStockCount(key, selectedSectorsList) {
        const sectors = singleIndexSectors[key] || [];
        let total = 0;
        for (const sec of sectors) {
            if (!selectedSectorsList.includes(sec)) continue;
            const inds = singleIndexIndustries[`${sec}_${key}`] || [];
            const secFilter = singleSelectedIndustries[sec] || [];
            for (const ind of inds) {
                if (secFilter.length === 0 || secFilter.includes(ind.industry)) {
                    total += ind.total || 0;
                }
            }
        }
        return total;
    }

    function getSectorStockCount(sec, key) {
        const inds = singleIndexIndustries[`${sec}_${key}`] || [];
        const secFilter = singleSelectedIndustries[sec] || [];
        let total = 0;
        for (const ind of inds) {
            if (secFilter.length === 0 || secFilter.includes(ind.industry)) {
                total += ind.total || 0;
            }
        }
        return total;
    }

    // --- SINGLE-INDEX DATA LOADING ---

    async function loadSingleIndexIndustries(sector) {
        if (!sector) return;
        const indicesStr = [...$sectorSelectedIndices].sort().join(',') || Object.keys(INDEX_CONFIG).sort().join(',');
        const cacheKey = `${sector}_${indicesStr}`;
        if (allSectorIndustries[cacheKey]) {
            singleIndexIndustries = { ...singleIndexIndustries, [sector]: allSectorIndustries[cacheKey] };
            return;
        }
        try {
            const { API_BASE_URL } = await import('$lib/config.js');
            const res = await fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sector)}&indices=${indicesStr}`);
            if (res.ok) {
                const data = await res.json();
                allSectorIndustries[cacheKey] = data;
                singleIndexIndustries = { ...singleIndexIndustries, [sector]: data };
            }
        } catch {}
    }

    // load sectors and preload all industry data for a given index
    async function _loadSingleIndexData(key) {
        let sectors = singleIndexSectors[key];
        if (!sectors) {
            try {
                const { API_BASE_URL } = await import('$lib/config.js');
                const res = await fetch(`${API_BASE_URL}/sector-comparison/sectors?indices=${key}`);
                if (res.ok) {
                    sectors = await res.json();
                    singleIndexSectors = { ...singleIndexSectors, [key]: sectors };
                }
            } catch {}
        }

        if (sectors && sectors.length > 0) {
            for (const sec of sectors) {
                const ck = `${sec}_${key}`;
                if (allSectorIndustries[ck]) {
                    singleIndexIndustries = { ...singleIndexIndustries, [ck]: allSectorIndustries[ck] };
                } else {
                    try {
                        const { API_BASE_URL } = await import('$lib/config.js');
                        const res = await fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sec)}&indices=${key}`);
                        if (res.ok) {
                            const data = await res.json();
                            allSectorIndustries[ck] = data;
                            singleIndexIndustries = { ...singleIndexIndustries, [ck]: data };
                        }
                    } catch {}
                }
            }
        }
    }

    // expand a different index in single-index mode (clicking same index is a no-op)
    async function openSingleIndex(key) {
        if (singleOpenIndex === key) return;

        singleOpenIndex = key;
        singleOpenSectors = new Set();
        singleSelectedIndex.set([key]);
        try { sessionStorage.setItem('dash_single_open_index', key); } catch {}

        await _loadSingleIndexData(key);
    }

    // --- SINGLE-INDEX SECTOR/INDUSTRY TOGGLES ---

    // expand or collapse a sector within the active index, loading industries on first open
    function toggleSingleSector(sec) {
        const next = new Set(singleOpenSectors);
        if (next.has(sec)) {
            next.delete(sec);
        } else {
            next.add(sec);
            const idxKey = singleOpenIndex;
            const cacheKey = `${sec}_${idxKey}`;
            if (!allSectorIndustries[cacheKey]) {
                import('$lib/config.js').then(({ API_BASE_URL }) => {
                    fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sec)}&indices=${idxKey}`)
                        .then(r => r.ok ? r.json() : [])
                        .then(data => {
                            allSectorIndustries[cacheKey] = data;
                            singleIndexIndustries = { ...singleIndexIndustries, [`${sec}_${idxKey}`]: data };
                        });
                });
            } else {
                singleIndexIndustries = { ...singleIndexIndustries, [`${sec}_${idxKey}`]: allSectorIndustries[cacheKey] };
            }
        }
        singleOpenSectors = next;

        toggleMultiSector(sec);
    }

    // per-sector industry toggle for cross-index mode (same ctrl+click logic as single-index)
    function toggleCrossIndustry(sector, industry, allInds, exclusive = false) {
        const current = $crossSelectedIndustries[sector] || [];
        let next;
        if (exclusive) {
            next = (current.length === 1 && current[0] === industry) ? [] : [industry];
        } else if (current.length === 0) {
            next = allInds.filter(i => i !== industry);
        } else if (current.includes(industry)) {
            const filtered = current.filter(i => i !== industry);
            next = filtered.length === 0 ? [] : filtered;
        } else {
            const updated = [...current, industry];
            next = updated.length >= allInds.length ? [] : updated;
        }
        crossSelectedIndustries.update(m => ({ ...m, [sector]: next }));
        // sync to flat store for the active sector's chart
        if (sector === $selectedSector) {
            selectedIndustries.set(next);
        }
    }

    function clearCrossIndustries(sector) {
        crossSelectedIndustries.update(m => ({ ...m, [sector]: [] }));
        if (sector === $selectedSector) selectedIndustries.set([]);
    }

    // per-sector industry toggle for single-index mode (same ctrl+click logic as cross-index)
    function toggleSingleIndustry(sector, industry, allIndustries, exclusive = false) {
        const current = singleSelectedIndustries[sector] || [];
        if (exclusive) {
            if (current.length === 1 && current[0] === industry) {
                singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: [] };
            } else {
                singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: [industry] };
            }
            return;
        }
        if (current.length === 0) {
            singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: allIndustries.filter(i => i !== industry) };
        } else if (current.includes(industry)) {
            const newList = current.filter(i => i !== industry);
            singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: newList.length === 0 ? [] : newList };
        } else {
            const newList = [...current, industry];
            singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: newList.length >= allIndustries.length ? [] : newList };
        }
    }

    function clearSingleIndustries(sector) {
        singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: [] };
    }

    function selectAllForIndex(key) {
        const sectors = singleIndexSectors[key] || availableSectors || [];
        selectedSectors.set([...sectors]);
        singleSelectedIndustries = {};
    }

    function toggleMultiSector(sec) {
        selectedSectors.update(list => {
            if (list.includes(sec)) {
                return list.length > 1 ? list.filter(s => s !== sec) : list;
            }
            return [...list, sec];
        });
    }

    // --- CROSS-INDEX INDUSTRY LOADING ---

    // fetch industries for a sector, serving from cache when possible to avoid UI flash
    async function loadIndustries(sector) {
        if (!sector) { sectorIndustries = []; return; }
        const indicesStr = [...$sectorSelectedIndices].sort().join(',') || Object.keys(INDEX_CONFIG).sort().join(',');
        const cacheKey = `${sector}_${indicesStr}`;
        if (allSectorIndustries[cacheKey]) {
            sectorIndustries = allSectorIndustries[cacheKey];
            return;
        }
        // keep current list visible during load to avoid blank-then-reload flash
        const prevSector = _lastLoadedSector;
        _lastLoadedSector = sector;
        if (prevSector !== sector) sectorIndustries = [];
        try {
            const { API_BASE_URL } = await import('$lib/config.js');
            const res = await fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sector)}&indices=${indicesStr}`);
            if (res.ok) {
                const data = await res.json();
                sectorIndustries = data;
                allSectorIndustries = { ...allSectorIndustries, [cacheKey]: data };
            }
        } catch { sectorIndustries = []; }
    }

    // industry lists for other sectors load on-demand when the user opens a sector panel

    // cross-index mode: expand a sector = select it. only one sector open at a time.
    // clicking the already-open sector collapses it (but keeps it selected for heatmap).
    function toggleSectorPanel(sec) {
        if (sectorPanelOpen.has(sec)) {
            // the active sector cannot be collapsed — ignore
            return;
        } else {
            // select this sector, collapse all others, open this one
            selectedSector.set(sec);
            sectorPanelOpen = new Set([sec]);
            loadIndustries(sec);
            // sync selectedIndustries from per-sector filter (only if actually different)
            const perSectorFilter = $crossSelectedIndustries[sec] || [];
            if (JSON.stringify(perSectorFilter) !== JSON.stringify($selectedIndustries)) {
                selectedIndustries.set(perSectorFilter);
            }
        }
    }

    // --- DERIVED STOCK COUNTS ---

    // total stocks in current sector across selected indices, filtered by industry selection
    let sectorStockCount = $derived((() => {
        if (!sectorIndustries.length) return 0;
        const included = sectorIndustries.filter(ind =>
            $selectedIndustries.length === 0 || $selectedIndustries.includes(ind.industry)
        );
        return included.reduce((sum, ind) => sum + (ind.total || 0), 0);
    })());

    // per-index stock counts for the currently expanded sector
    let indexStockCounts = $derived((() => {
        const counts = {};
        if (!sectorIndustries.length) return counts;
        const included = sectorIndustries.filter(ind =>
            $selectedIndustries.length === 0 || $selectedIndustries.includes(ind.industry)
        );
        for (const ind of included) {
            for (const [idx, cnt] of Object.entries(ind.indices || {})) {
                counts[idx] = (counts[idx] || 0) + cnt;
            }
        }
        return counts;
    })());

    async function loadAvailableSectors() {
        if (sectorsLoaded) return;
        try {
            const allKeys = Object.keys(INDEX_CONFIG).join(',');
            const res = await fetch(`${import('$lib/config.js').then(m => m.API_BASE_URL)}/sector-comparison/sectors?indices=${allKeys}`);
            if (res.ok) availableSectors = await res.json();
        } catch {}
        sectorsLoaded = true;
    }

    // --- REACTIVE: SECTOR MODE INITIALIZATION ---

    $effect(() => {
        // fetch available sectors on first entry
        if (inSectors && !sectorsLoaded) {
            import('$lib/config.js').then(({ API_BASE_URL }) => {
                const allKeys = Object.keys(INDEX_CONFIG).join(',');
                fetch(`${API_BASE_URL}/sector-comparison/sectors?indices=${allKeys}`)
                    .then(r => r.ok ? r.json() : [])
                    .then(data => {
                        availableSectors = data;
                        sectorsLoaded = true;
                        // default to first sector in list order on fresh session
                        if (data.length > 0 && _sectorDefaultNeeded) {
                            _sectorDefaultNeeded = false;
                            try { sessionStorage.setItem('dash_sector_visited', '1'); } catch {}
                            const first = data[0];
                            selectedSector.set(first);
                            _crossSector = first;
                            _singleSector = first;
                            _lastEffectSector = '';
                        }
                    })
                    .catch(() => { sectorsLoaded = true; });
            });
        }
        // reset on mode switch so expansion re-triggers for the current sector
        if (inSectors && $sectorAnalysisMode !== _lastEffectMode) {
            _lastEffectMode = $sectorAnalysisMode;
            _lastEffectSector = '';
        }
        // cross-index: when selectedSector changes (from heatmap or sidebar), expand only it, scroll
        if (inSectors && $selectedSector && $sectorAnalysisMode === 'cross-index') {
            _crossSector = $selectedSector;
            const sectorChanged = $selectedSector !== _lastEffectSector;
            if (sectorChanged) {
                _lastEffectSector = $selectedSector;
                // collapse all others, open only the selected sector
                sectorPanelOpen = new Set([$selectedSector]);
                // sync selectedIndustries to the new sector's per-sector filter (only if actually different).
                // untrack the read to avoid adding $selectedIndustries as a dependency of this effect.
                const perSectorFilter = $crossSelectedIndustries[$selectedSector] || [];
                const currentInds = untrack(() => $selectedIndustries);
                if (JSON.stringify(perSectorFilter) !== JSON.stringify(currentInds)) {
                    selectedIndustries.set(perSectorFilter);
                }
                loadIndustries($selectedSector);
                tick().then(() => {
                    const el = document.querySelector(`[data-sector-cross="${CSS.escape($selectedSector)}"]`);
                    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            }
        }
        // single-index: when selectedSector changes (from rankings), expand and scroll to it
        if (inSectors && $selectedSector && $sectorAnalysisMode === 'single-index') {
            _singleSector = $selectedSector;
            const sectorChanged = $selectedSector !== _lastEffectSector;
            if (sectorChanged) {
                _lastEffectSector = $selectedSector;
                const sec = $selectedSector;
                if (!singleOpenSectors.has(sec)) {
                    const next = new Set(singleOpenSectors);
                    next.add(sec);
                    singleOpenSectors = next;
                    const idxKey = singleOpenIndex;
                    const ck = `${sec}_${idxKey}`;
                    if (!allSectorIndustries[ck]) {
                        import('$lib/config.js').then(({ API_BASE_URL: base }) => {
                            fetch(`${base}/sector-comparison/industries?sector=${encodeURIComponent(sec)}&indices=${idxKey}`)
                                .then(r => r.ok ? r.json() : [])
                                .then(data => { allSectorIndustries[ck] = data; singleIndexIndustries = { ...singleIndexIndustries, [ck]: data }; });
                        });
                    } else {
                        singleIndexIndustries = { ...singleIndexIndustries, [ck]: allSectorIndustries[ck] };
                    }
                }
                tick().then(() => {
                    const el = document.querySelector(`[data-sector-single="${CSS.escape(sec)}"]`);
                    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            }
        }
    });

    // --- REACTIVE: SYNC SINGLE-INDEX INDUSTRY FILTERS TO GLOBAL STORE ---

    // merge per-sector industry selections into the global selectedIndustries store for the API.
    // also publish the per-sector map to singleModeIndustries so +page.svelte can apply per-sector filtering.
    $effect(() => {
        if ($sectorAnalysisMode !== 'single-index') return;
        const perSector = singleSelectedIndustries;
        // publish per-sector map for +page.svelte chart filtering
        singleModeIndustries.set({ ...perSector });
        let anyFiltered = false;
        for (const [sec, inds] of Object.entries(perSector)) {
            if (inds.length > 0) { anyFiltered = true; break; }
        }
        if (!anyFiltered) {
            if ($selectedIndustries.length > 0) selectedIndustries.set([]);
            return;
        }
        const combined = new Set();
        const activeKey = singleOpenIndex;
        const activeSectors = singleIndexSectors[activeKey] || [];
        for (const sec of activeSectors) {
            if (!$selectedSectors.includes(sec)) continue;
            const secFilter = perSector[sec] || [];
            const allInds = singleIndexIndustries[`${sec}_${activeKey}`] || [];
            if (secFilter.length > 0) {
                for (const name of secFilter) combined.add(name);
            } else {
                for (const ind of allInds) combined.add(ind.industry);
            }
        }
        selectedIndustries.set(Array.from(combined));
    });

    // --- SECTOR TREE (STOCK BROWSING MODE) ---

    // build sector > industry > stocks hierarchy, filtered by search query
    let sectorTree = $derived((() => {
        const filtered = tickers.filter(t =>
            !searchQuery ||
            t.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
            (t.name && t.name.toLowerCase().includes(searchQuery.toLowerCase()))
        );
        const sectorMap = {};
        filtered.forEach(t => {
            const sector = (t.sector && t.sector !== 0) ? t.sector : 'Other';
            const industry = (t.industry && t.industry !== 0) ? t.industry : 'General';
            if (!sectorMap[sector]) sectorMap[sector] = {};
            if (!sectorMap[sector][industry]) sectorMap[sector][industry] = [];
            sectorMap[sector][industry].push(t);
        });
        return Object.keys(sectorMap).sort().map(sector => ({
            name: sector,
            industries: Object.keys(sectorMap[sector]).sort().map(industry => ({
                name: industry,
                stocks: sectorMap[sector][industry].sort((a, b) => a.symbol.localeCompare(b.symbol)),
            })),
            stockCount: Object.values(sectorMap[sector]).flat().length,
        }));
    })());

    // --- SECTOR EXPAND/COLLAPSE HELPERS ---

    function openSectorFor(symbol) {
        const asset = tickers.find(t => t.symbol === symbol);
        if (!asset) return;
        const sectorName = (asset.sector && asset.sector !== 0) ? asset.sector : 'Other';
        if (!openSectors.has(sectorName)) {
            openSectors = new Set([...openSectors, sectorName]);
        }
    }

    // collapse all sectors then open only the one containing this symbol
    function resetSectorsTo(symbol, tickerList) {
        const asset = tickerList.find(t => t.symbol === symbol);
        if (!asset) { openSectors = new Set(); return; }
        const sectorName = (asset.sector && asset.sector !== 0) ? asset.sector : 'Other';
        openSectors = new Set([sectorName]);
    }

    // --- REACTIVE: EXTERNAL FOCUS REQUEST ---

    // handle focus requests from TopMovers/MarketLeaders: open sector + scroll into view.
    // uses a seq counter so re-clicking the same symbol still triggers.
    let lastFocusSeq = 0;
    $effect(() => {
        const req = $focusSymbolRequest;
        if (!req.symbol || req.seq === lastFocusSeq) return;
        lastFocusSeq = req.seq;

        if (tickers.length === 0) return;
        openSectorFor(req.symbol);

        if (!scrollContainer) return;
        setTimeout(() => {
            const el = scrollContainer.querySelector(`[data-symbol="${CSS.escape(req.symbol)}"]`);
            if (el) {
                const cr = scrollContainer.getBoundingClientRect();
                const er = el.getBoundingClientRect();
                scrollContainer.scrollTop += er.top - cr.top - (cr.height / 2) + (er.height / 2);
            }
        }, 50);
    });

    // --- GENERAL UI HANDLERS ---

    function toggleSector(sector) {
        const next = new Set(openSectors);
        next.has(sector) ? next.delete(sector) : next.add(sector);
        openSectors = next;
    }

    function handleClickOutside(e) {
        if (dropdownOpen && !e.target.closest('.index-dropdown')) dropdownOpen = false;
    }

    // --- LIFECYCLE ---

    onMount(async () => {
        document.addEventListener('click', handleClickOutside);
        if (!$summaryData.loaded && !$summaryData.loading) {
            const data = await loadSummaryData($marketIndex);
            if (data && data.length > 0) {
                resetSectorsTo($selectedSymbol, data);
            }
        } else if ($summaryData.assets.length > 0) {
            resetSectorsTo($selectedSymbol, $summaryData.assets);
        }

        // restore persisted single-index expansion
        if ($sectorAnalysisMode === 'single-index' && singleOpenIndex) {
            singleSelectedIndex.set([singleOpenIndex]);
            _loadSingleIndexData(singleOpenIndex);
        }

        return () => document.removeEventListener('click', handleClickOutside);
    });

    // --- INDEX SWITCHING ---

    // remember the last selected symbol per index so switching back restores it
    let lastSymbolPerIndex = Object.fromEntries(
        Object.entries(INDEX_CONFIG).map(([key, cfg]) => [key, cfg.defaultSymbol])
    );

    function selectTicker(symbol) {
        selectedSymbol.set(symbol);
        lastSymbolPerIndex[$marketIndex] = symbol;
        openSectorFor(symbol);
    }

    async function switchIndex(key) {
        if (key === currentIndex) return;
        lastSymbolPerIndex[currentIndex] = $selectedSymbol;
        openSectors = new Set();
        marketIndex.set(key);
        searchQuery = '';
        dropdownOpen = false;
        const newTickers = await loadSummaryData(key);
        const newSymbol = lastSymbolPerIndex[key] || INDEX_CONFIG[key]?.defaultSymbol || '';
        selectedSymbol.set(newSymbol);
        if (newTickers && newTickers.length > 0) {
            resetSectorsTo(newSymbol, newTickers);
        }
    }

    // --- FORMATTERS ---

    function formatVol(val) {
        if (!val) return '0';
        if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
        if (val >= 1000) return (val / 1000).toFixed(0) + 'K';
        return val.toString();
    }

    export function openSectorForSymbol(symbol) {
        openSectorFor(symbol);
    }
</script>

<aside class="flex flex-col h-full bg-bloom-card border-r border-bloom-muted/10 relative z-20 shadow-2xl overflow-hidden">

    <div class="p-4 space-y-3 bg-gradient-to-b from-white/5 to-transparent">

        <!-- index dropdown selector -->
        <div class="relative index-dropdown">
            <div class="text-[20px] font-black text-white/25 uppercase tracking-[0.1em] mb-2.5 text-center">Global Exchange Monitor</div>

            <button
                onclick={() => dropdownOpen = !dropdownOpen}
                class="w-full flex items-center justify-between bg-black/40 border border-bloom-muted/20 rounded-xl px-4 py-3 hover:border-bloom-accent/40 transition-all group"
            >
                <div class="flex items-center gap-3">
                    {#if inOverview}
                        <svg class="w-6 h-6 text-bloom-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <circle cx="12" cy="12" r="10" stroke-width="1.5"/>
                            <path stroke-width="1.5" d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/>
                        </svg>
                        <span class="text-base font-black text-white/85">INDEX OVERVIEW</span>
                    {:else if inSectors}
                        <svg class="w-6 h-6 text-bloom-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-width="1.5" d="M4 6h4v4H4zM10 6h4v4h-4zM16 6h4v4h-4zM4 14h4v4H4zM10 14h4v4h-4zM16 14h4v4h-4z"/>
                        </svg>
                        <span class="text-base font-black text-white/85">SECTOR ANALYSIS</span>
                    {:else}
                        <span class="{INDEX_FLAGS[currentIndex] || ''} fis rounded-sm" style="font-size: 1.4rem;"></span>
                        <span class="text-base font-black text-white/85">{currentConfig?.shortLabel}</span>
                        <span class="text-[14px] font-bold text-cyan-400 uppercase ml-1">{currentConfig?.currencyCode}</span>
                        <span class="text-[20px] font-black text-cyan-400/60">{currentConfig?.currency}</span>
                    {/if}
                </div>
                <svg class="w-4 h-4 text-white/30 group-hover:text-bloom-accent transition-all {dropdownOpen ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
            </button>

            {#if dropdownOpen}
                <div class="absolute top-full left-0 right-0 mt-2 bg-[#16161e] border border-white/10 rounded-2xl shadow-2xl z-50 overflow-hidden">
                    <!-- index overview option -->
                    <button
                        onclick={switchToOverview}
                        class="w-full flex items-center justify-between px-4 py-3.5 hover:bg-white/5 transition-all border-b border-white/10
                        {inOverview ? 'bg-bloom-accent/10 border-l-[3px] border-l-bloom-accent' : 'border-l-[3px] border-l-transparent'}"
                    >
                        <div class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-bloom-accent/70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <circle cx="12" cy="12" r="10" stroke-width="1.5"/>
                                <path stroke-width="1.5" d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/>
                            </svg>
                            <span class="text-[15px] font-bold text-white/80">INDEX OVERVIEW</span>
                        </div>
                        {#if inOverview}
                            <span class="w-2 h-2 rounded-full bg-bloom-accent shadow-[0_0_6px_rgba(168,85,247,0.6)]"></span>
                        {/if}
                    </button>

                    <!-- sector analysis option -->
                    <button
                        onclick={switchToSectors}
                        class="w-full flex items-center justify-between px-4 py-3.5 hover:bg-white/5 transition-all border-b border-white/10
                        {inSectors ? 'bg-bloom-accent/10 border-l-[3px] border-l-bloom-accent' : 'border-l-[3px] border-l-transparent'}"
                    >
                        <div class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-bloom-accent/70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-width="1.5" d="M4 6h4v4H4zM10 6h4v4h-4zM16 6h4v4h-4zM4 14h4v4H4zM10 14h4v4h-4zM16 14h4v4h-4z"/>
                            </svg>
                            <span class="text-[15px] font-bold text-white/80">SECTOR ANALYSIS</span>
                        </div>
                        {#if inSectors}
                            <span class="w-2 h-2 rounded-full bg-bloom-accent shadow-[0_0_6px_rgba(168,85,247,0.6)]"></span>
                        {/if}
                    </button>

                    {#each INDEX_GROUPS as group}
                        <div class="px-4 py-2 text-[9px] font-black text-white/20 uppercase tracking-widest border-b border-white/5">{group.label}</div>
                        {#each group.indices as idx}
                            <button
                                onclick={() => switchIndex(idx.key)}
                                class="w-full flex items-center justify-between px-4 py-3.5 hover:bg-white/5 transition-all
                                {idx.key === currentIndex && !inOverview && !inSectors ? 'bg-bloom-accent/10 border-l-[3px] border-l-bloom-accent' : 'border-l-[3px] border-l-transparent'}"
                            >
                                <div class="flex items-center gap-3">
                                    <span class="{INDEX_FLAGS[idx.key] || ''} fis rounded-sm" style="font-size: 1.3rem;"></span>
                                    <span class="text-[15px] font-bold text-white/80">{idx.shortLabel}</span>
                                </div>
                                <div class="flex items-center gap-2 pr-2">
                                    <span class="text-[13px] font-bold text-cyan-400/80">{idx.currencyCode}</span>
                                    <span class="text-[16px] font-black text-cyan-400/60">{idx.currency}</span>
                                    {#if idx.key === currentIndex && !inOverview && !inSectors}
                                        <span class="ml-1 w-2 h-2 rounded-full bg-bloom-accent shadow-[0_0_6px_rgba(168,85,247,0.6)]"></span>
                                    {/if}
                                </div>
                            </button>
                        {/each}
                    {/each}
                </div>
            {/if}
        </div>

        <!-- search bar + expand/collapse (hidden in overview and sector modes) -->
        {#if !inOverview && !inSectors}
            <div class="flex gap-2">
                <div class="relative group flex-1">
                    <input type="text" bind:value={searchQuery} placeholder="Search symbol or name..."
                        class="w-full bg-black/40 border border-bloom-muted/20 rounded-xl py-2.5 px-4 pl-10 text-sm font-bold text-white placeholder:text-bloom-text/20 focus:outline-none focus:border-bloom-accent/50 focus:ring-4 focus:ring-bloom-accent/10 transition-all"
                    />
                    <svg class="absolute left-3 top-3 w-4 h-4 text-bloom-text/20 group-focus-within:text-bloom-accent transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                </div>
                <button onclick={() => { openSectors = new Set(sectorTree.map(s => s.name)); }} title="Expand all"
                    class="shrink-0 w-9 flex items-center justify-center bg-black/40 border border-bloom-muted/20 rounded-xl hover:border-bloom-accent/40 transition-all text-white/30 hover:text-bloom-accent"
                >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                    </svg>
                </button>
                <button onclick={() => { openSectors = new Set(); }} title="Collapse all"
                    class="shrink-0 w-9 flex items-center justify-center bg-black/40 border border-bloom-muted/20 rounded-xl hover:border-bloom-accent/40 transition-all text-white/30 hover:text-bloom-accent"
                >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
                    </svg>
                </button>
            </div>
        {/if}
    </div>

    <div bind:this={scrollContainer} class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar">
        {#if inSectors}
            <!-- sector analysis sidebar -->
            <div class="flex flex-col h-full">
                <div class="p-4 pb-2 space-y-3 shrink-0">
                <!-- mode toggle: cross-index vs single-index -->
                <div class="flex bg-black/40 border border-white/10 rounded-xl p-1 gap-1">
                    <button onclick={() => { _singleSector = $selectedSector; selectedSector.set(_crossSector); sectorAnalysisMode.set('cross-index'); }}
                        class="flex-1 text-center py-2 rounded-lg text-[11px] font-black uppercase tracking-wider transition-all
                        {$sectorAnalysisMode === 'cross-index' ? 'bg-orange-500/20 text-white border border-orange-500/30' : 'text-white/30 hover:text-white/50 border border-transparent'}">
                        Cross-Index
                    </button>
                    <button onclick={() => { _crossSector = $selectedSector; selectedSector.set(_singleSector); sectorAnalysisMode.set('single-index'); }}
                        class="flex-1 text-center py-2 rounded-lg text-[11px] font-black uppercase tracking-wider transition-all
                        {$sectorAnalysisMode === 'single-index' ? 'bg-orange-500/20 text-white border border-orange-500/30' : 'text-white/30 hover:text-white/50 border border-transparent'}">
                        Single-Index
                    </button>
                </div>

                {#if $sectorAnalysisMode === 'cross-index'}
                    <!-- index checkboxes (cross-index mode) -->
                    <div class="space-y-1">
                        <div class="flex items-center justify-between mb-1">
                            <span class="text-[10px] font-black text-white/30 uppercase tracking-widest">Select Indices</span>
                            {#if sectorStockCount > 0}
                                <span class="text-[12px] font-black text-white/30 tabular-nums">{sectorStockCount}</span>
                            {/if}
                        </div>
                        {#each Object.entries(INDEX_CONFIG) as [key, cfg]}
                            {@const isSelected = $sectorSelectedIndices.includes(key)}
                            {@const idxCount = indexStockCounts[key] || 0}
                            <button
                                onclick={() => toggleSectorIndex(key)}
                                class="w-full flex items-center gap-3 px-3 py-2 rounded-xl transition-all
                                {isSelected ? 'bg-white/[0.06] border border-white/10' : 'bg-transparent border border-transparent hover:bg-white/[0.03]'}"
                            >
                                <div class="w-3.5 h-3.5 rounded-sm border flex items-center justify-center shrink-0 transition-all
                                    {isSelected ? 'border-white/40 bg-white/20' : 'border-white/15 bg-transparent'}">
                                    {#if isSelected}
                                        <svg class="w-2.5 h-2.5 text-white/80" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                        </svg>
                                    {/if}
                                </div>
                                <span class="{INDEX_FLAGS[key] || ''} fis rounded-sm" style="font-size: 1.1rem;"></span>
                                <span class="text-[13px] font-bold text-white/70">{cfg.shortLabel}</span>
                                {#if isSelected && idxCount > 0}
                                    <span class="ml-auto text-[12px] font-bold text-white/25 tabular-nums">{idxCount}</span>
                                {/if}
                            </button>
                        {/each}
                    </div>
                {:else}
                    <div class="text-[10px] font-black text-white/30 uppercase tracking-widest">Browse by Index</div>
                {/if}
            </div>

                {#if $sectorAnalysisMode === 'cross-index'}
                    <!-- cross-index sector tree -->
                    <div class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar">
                        {#each (availableSectors.length > 0 ? availableSectors : ['Basic Materials', 'Communication Services', 'Consumer Cyclical', 'Consumer Defensive', 'Energy', 'Financial Services', 'Healthcare', 'Industrials', 'Real Estate', 'Technology', 'Utilities']) as sec}
                            {@const isOpen = sectorPanelOpen.has(sec)}
                            {@const isActive = $selectedSector === sec}
                            {@const isCurrent = isOpen && isActive}
                            <button data-sector-cross={sec}
                                onclick={() => toggleSectorPanel(sec)}
                                class="w-full flex items-center justify-between px-4 py-3 hover:bg-[#1e1e2a] border-b border-white/[0.06] transition-all relative overflow-hidden
                                {isActive ? 'bg-orange-500/15' : 'bg-[#1a1a24] border-l-[3px] border-l-white/10'}"
                            >
                                {#if isActive}
                                    <div class="absolute left-0 top-0 bottom-0 w-[3px] bg-orange-500 shadow-[0_0_10px_rgba(249,115,22,0.4)]"></div>
                                {/if}
                                <div class="flex items-center gap-2.5">
                                    <svg class="w-3 h-3 {isActive ? 'text-orange-400/60' : 'text-white/40'} transition-transform {isOpen ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                                    </svg>
                                    <span class="text-[11px] font-black uppercase tracking-widest {isActive ? 'text-white/90' : 'text-white/80'}">{sec}</span>
                                </div>
                                <div class="flex items-center gap-2">
                                    {#if isCurrent}
                                        <span class="text-[12px] font-bold text-white/25 tabular-nums">{sectorStockCount}</span>
                                    {/if}
                                </div>
                            </button>

                            {#if isOpen}
                                {@const indicesStr = [...$sectorSelectedIndices].sort().join(',') || Object.keys(INDEX_CONFIG).sort().join(',')}
                                {@const cacheKey = `${sec}_${indicesStr}`}
                                {@const secInds = allSectorIndustries[cacheKey] || []}
                                {#if allSectorIndustries[cacheKey] && secInds.length === 0}
                                    <div class="px-4 py-3 ml-2 border-l-[2px] border-l-white/5 bg-[#13131a]">
                                        <span class="text-[10px] font-bold text-white/15 uppercase tracking-widest">No data for selected indices</span>
                                    </div>
                                {:else if secInds.length > 0}
                                    {@const allIndNames = secInds.map(i => i.industry)}
                                    {@const secFilter = $crossSelectedIndustries[sec] || []}
                                    <div class="flex items-center justify-between px-4 py-[6px] ml-2 border-l-[2px] border-l-amber-500/20 bg-[#13131a]">
                                        <span class="text-[9px] font-black text-white/20 uppercase tracking-widest">
                                            {secFilter.length === 0 ? `${secInds.length} industries` : `${secFilter.length} of ${secInds.length}`}
                                        </span>
                                        {#if secFilter.length > 0}
                                            <button onclick={() => clearCrossIndustries(sec)}
                                                class="text-[9px] font-bold uppercase tracking-wider text-amber-400/70 hover:text-amber-400 transition-all">
                                                Select All
                                            </button>
                                        {:else}
                                            <span class="text-[9px] font-bold text-white/15 uppercase tracking-wider">Ctrl+click to isolate</span>
                                        {/if}
                                    </div>
                                    {#each secInds as ind}
                                        {@const isChecked = secFilter.length === 0 || secFilter.includes(ind.industry)}
                                        <button onclick={(e) => toggleCrossIndustry(sec, ind.industry, allIndNames, e.ctrlKey || e.metaKey)}
                                            class="w-full flex items-center gap-2.5 px-4 py-[7px] ml-2 border-l-[2px] bg-[#13131a] transition-all
                                            {isChecked ? 'border-l-amber-500/40' : 'border-l-white/5'}"
                                            title="Click to toggle, Ctrl+click to select only this one"
                                        >
                                            <div class="w-3.5 h-3.5 rounded-sm border flex items-center justify-center shrink-0
                                                {isChecked ? 'border-amber-500/60 bg-amber-500/20' : 'border-white/10 bg-transparent'}">
                                                {#if isChecked}
                                                    <svg class="w-2.5 h-2.5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                                    </svg>
                                                {/if}
                                            </div>
                                            <span class="text-[11px] font-semibold uppercase tracking-wide truncate {isChecked ? 'text-white/55' : 'text-white/20'}">{ind.industry}</span>
                                            <span class="ml-auto text-[12px] text-white/30 tabular-nums font-bold shrink-0">{ind.total}</span>
                                        </button>
                                    {/each}
                                {:else}
                                    <div class="px-6 py-2 ml-2 border-l-[2px] border-l-white/5 bg-[#13131a]">
                                        <div class="w-3 h-3 border border-white/10 border-t-white/30 rounded-full animate-spin"></div>
                                    </div>
                                {/if}
                            {/if}
                        {/each}
                    </div>
                {:else}
                    <!-- single-index: index > sector > industry tree -->
                    <div class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar">
                        {#each Object.entries(INDEX_CONFIG) as [key, cfg]}
                            {@const isIndexOpen = singleOpenIndex === key}
                            {@const indexSelectedStocks = (() => {
                                const sectors = singleIndexSectors[key] || [];
                                let total = 0;
                                for (const s of sectors) {
                                    if (!$selectedSectors.includes(s)) continue;
                                    const inds = singleIndexIndustries[`${s}_${key}`] || [];
                                    const sf = singleSelectedIndustries[s] || [];
                                    for (const ind of inds) {
                                        if (sf.length === 0 || sf.includes(ind.industry)) total += ind.total || 0;
                                    }
                                }
                                return total;
                            })()}
                            <!-- index row (top-level) -->
                            <button onclick={() => openSingleIndex(key)}
                                class="w-full flex items-center justify-between px-4 py-3.5 bg-[#1a1a24] hover:bg-[#1e1e2a] border-b border-white/[0.06] transition-all relative overflow-hidden
                                {isIndexOpen ? 'bg-orange-500/15' : 'border-l-[3px] border-l-white/10'}"
                            >
                                {#if isIndexOpen}
                                    <div class="absolute left-0 top-0 bottom-0 w-[3px] bg-orange-500 shadow-[0_0_10px_rgba(249,115,22,0.4)]"></div>
                                {/if}
                                <div class="flex items-center gap-3">
                                    <svg class="w-3.5 h-3.5 text-white/40 transition-transform {isIndexOpen ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                                    </svg>
                                    <span class="{INDEX_FLAGS[key] || ''} fis rounded-sm" style="font-size: 1.3rem;"></span>
                                    <span class="text-[13px] font-black uppercase tracking-widest {isIndexOpen ? 'text-white' : 'text-white/70'}">{cfg.shortLabel}</span>
                                </div>
                                {#if isIndexOpen && indexSelectedStocks > 0}
                                    <span class="text-[12px] font-bold text-white/25 tabular-nums">{indexSelectedStocks}</span>
                                {/if}
                            </button>

                            {#if isIndexOpen}
                                {@const sectors = singleIndexSectors[key] || availableSectors || []}
                                {@const hasAnyIndustryFilter = sectors.some(s => (singleSelectedIndustries[s] || []).length > 0)}
                                <!-- sector select-all / count header -->
                                <div class="flex items-center justify-between px-4 py-[6px] ml-2 border-l-[2px] border-l-bloom-accent/10 bg-[#15151e]">
                                    <span class="text-[9px] font-black text-white/20 uppercase tracking-widest">
                                        {$selectedSectors.length} of {sectors.length} sectors
                                    </span>
                                    {#if $selectedSectors.length < sectors.length || hasAnyIndustryFilter}
                                        <button onclick={() => selectAllForIndex(key)}
                                            class="text-[9px] font-bold uppercase tracking-wider text-amber-400/70 hover:text-amber-400 transition-all">
                                            Select All
                                        </button>
                                    {:else}
                                        <span class="text-[9px] font-bold text-white/15 uppercase tracking-wider">Ctrl+click to isolate</span>
                                    {/if}
                                </div>
                                {#each sectors as sec, sIdx}
                                    {@const isSectorOpen = singleOpenSectors.has(sec)}
                                    {@const isSectorActive = $selectedSectors.includes(sec)}
                                    {@const isSelectedSec = $selectedSector === sec}
                                    {@const sectorPalette = ['#8b5cf6', '#06b6d4', '#f59e0b', '#ef4444', '#22c55e', '#ec4899', '#3b82f6', '#f97316', '#84cc16', '#a855f7', '#14b8a6']}
                                    {@const allSectorsRef = ['Basic Materials', 'Communication Services', 'Consumer Cyclical', 'Consumer Defensive', 'Energy', 'Financial Services', 'Healthcare', 'Industrials', 'Real Estate', 'Technology', 'Utilities']}
                                    {@const stableIdx = allSectorsRef.indexOf(sec)}
                                    {@const sColor = sectorPalette[(stableIdx >= 0 ? stableIdx : sIdx) % sectorPalette.length]}
                                    {@const secStockCount = (() => {
                                        const inds = singleIndexIndustries[`${sec}_${key}`] || [];
                                        const sf = singleSelectedIndustries[sec] || [];
                                        let t = 0;
                                        for (const ind of inds) {
                                            if (sf.length === 0 || sf.includes(ind.industry)) t += ind.total || 0;
                                        }
                                        return t;
                                    })()}
                                    {@const secIndustries = singleIndexIndustries[`${sec}_${key}`] || []}
                                    <!-- sector row -->
                                    <div data-sector-single={sec}
                                         class="flex items-center ml-2 border-b border-white/[0.04] transition-all border-l-[3px] relative
                                        {isSelectedSec ? 'bg-orange-500/15 border-l-orange-500' : 'bg-[#1a1a24] hover:bg-[#1e1e2a] border-l-bloom-accent/20'}">
                                        {#if isSelectedSec}
                                            <div class="absolute left-0 top-0 bottom-0 w-[3px] bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.5)]"></div>
                                        {/if}
                                        <!-- checkbox (ctrl+click for exclusive select) -->
                                        <button onclick={(e) => {
                                                e.stopPropagation();
                                                const allSecs = singleIndexSectors[singleOpenIndex] || availableSectors || [];
                                                if (e.ctrlKey || e.metaKey) {
                                                    if ($selectedSectors.length === 1 && $selectedSectors[0] === sec) {
                                                        selectedSectors.set([...allSecs]);
                                                    } else {
                                                        selectedSectors.set([sec]);
                                                    }
                                                } else {
                                                    toggleMultiSector(sec);
                                                }
                                            }}
                                            class="pl-3 pr-2.5 py-2.5 shrink-0 flex items-center justify-center"
                                            title="Click to toggle, Ctrl+click to select only this one"
                                        >
                                            <div class="w-3.5 h-3.5 rounded-sm border flex items-center justify-center transition-all
                                                {isSectorActive ? 'border-white/40 bg-white/20' : 'border-white/15 bg-transparent'}">
                                                {#if isSectorActive}
                                                    <svg class="w-2.5 h-2.5 text-white/80" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                                    </svg>
                                                {/if}
                                            </div>
                                        </button>
                                        <!-- expand/collapse chevron + sector name -->
                                        <button onclick={() => {
                                                const next = new Set(singleOpenSectors);
                                                if (next.has(sec)) { next.delete(sec); } else {
                                                    next.add(sec);
                                                    const idxKey = singleOpenIndex;
                                                    const ck = `${sec}_${idxKey}`;
                                                    if (!allSectorIndustries[ck]) {
                                                        import('$lib/config.js').then(({ API_BASE_URL: base }) => {
                                                            fetch(`${base}/sector-comparison/industries?sector=${encodeURIComponent(sec)}&indices=${idxKey}`)
                                                                .then(r => r.ok ? r.json() : [])
                                                                .then(data => { allSectorIndustries[ck] = data; singleIndexIndustries = { ...singleIndexIndustries, [ck]: data }; });
                                                        });
                                                    } else {
                                                        singleIndexIndustries = { ...singleIndexIndustries, [ck]: allSectorIndustries[ck] };
                                                    }
                                                }
                                                singleOpenSectors = next;
                                            }}
                                            class="flex-1 flex items-center gap-2.5 py-2.5 pr-4 min-w-0"
                                        >
                                            <svg class="w-3.5 h-3.5 text-white/30 transition-transform shrink-0 {isSectorOpen ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                                            </svg>
                                            <span class="text-[11px] font-semibold uppercase tracking-widest truncate
                                                {isSelectedSec ? 'text-white/90' : isSectorActive ? 'text-white/80' : 'text-white/50'}">
                                                {sec}
                                            </span>
                                            {#if secStockCount > 0}
                                                <span class="ml-auto text-[12px] font-bold text-white/25 tabular-nums shrink-0">{secStockCount}</span>
                                            {/if}
                                        </button>
                                    </div>

                                    <!-- industry checkboxes (level 3) with per-sector filtering -->
                                    {#if isSectorOpen}
                                        {#if secIndustries.length > 0}
                                            {@const secSelInds = singleSelectedIndustries[sec] || []}
                                            {@const allIndNames = secIndustries.map(i => i.industry)}
                                            <div class="flex items-center justify-between px-4 py-[5px] ml-4 border-l-[2px] border-l-amber-500/15 bg-[#12121a]">
                                                <span class="text-[9px] font-black text-white/15 uppercase tracking-widest">
                                                    {secSelInds.length === 0 ? `${secIndustries.length} industries` : `${secSelInds.length} of ${secIndustries.length}`}
                                                </span>
                                                {#if secSelInds.length > 0}
                                                    <button onclick={() => clearSingleIndustries(sec)}
                                                        class="text-[9px] font-bold uppercase tracking-wider text-amber-400/70 hover:text-amber-400 transition-all">
                                                        Select All
                                                    </button>
                                                {:else}
                                                    <span class="text-[9px] font-bold text-white/10 uppercase tracking-wider">Ctrl+click to isolate</span>
                                                {/if}
                                            </div>
                                            {#each secIndustries as ind}
                                                {@const isIndChecked = secSelInds.length === 0 || secSelInds.includes(ind.industry)}
                                                <button
                                                    onclick={(e) => toggleSingleIndustry(sec, ind.industry, allIndNames, e.ctrlKey || e.metaKey)}
                                                    class="w-full flex items-center gap-2.5 px-4 py-[6px] ml-4 border-l-[2px] bg-[#12121a] transition-all
                                                    {isIndChecked ? 'border-l-amber-500/30' : 'border-l-white/5'}"
                                                    title="Click to toggle, Ctrl+click to select only this one"
                                                >
                                                    <div class="w-3 h-3 rounded-sm border flex items-center justify-center shrink-0
                                                        {isIndChecked ? 'border-amber-500/60 bg-amber-500/20' : 'border-white/10 bg-transparent'}">
                                                        {#if isIndChecked}
                                                            <svg class="w-2 h-2 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                                            </svg>
                                                        {/if}
                                                    </div>
                                                    <span class="text-[11px] font-semibold uppercase tracking-wide truncate {isIndChecked ? 'text-white/50' : 'text-white/20'}">{ind.industry}</span>
                                                    <span class="ml-auto text-[12px] text-white/30 tabular-nums font-bold shrink-0">{ind.total}</span>
                                                </button>
                                            {/each}
                                        {:else}
                                            <div class="px-8 py-2 ml-4 border-l-[2px] border-l-white/5 bg-[#12121a]">
                                                <div class="w-3 h-3 border border-white/10 border-t-white/30 rounded-full animate-spin"></div>
                                            </div>
                                        {/if}
                                    {/if}
                                {/each}
                            {/if}
                        {/each}
                    </div>
                {/if}
            </div>
        {:else if inOverview}
            <!-- index overview: checkboxes for comparison -->
            <div class="p-4 space-y-2">
                <div class="text-[10px] font-black text-white/30 uppercase tracking-widest mb-3">Select indices to compare</div>
                {#each Object.entries(INDEX_CONFIG) as [key, cfg]}
                    {@const ticker = INDEX_KEY_TO_TICKER[key]}
                    {@const asset = overviewAssets.find(a => a.symbol === ticker)}
                    {@const isSelected = $overviewSelectedIndices.includes(ticker)}
                    <button
                        onclick={() => toggleOverviewIndex(ticker)}
                        class="w-full flex items-center gap-3 px-4 py-3.5 rounded-xl transition-all
                        {isSelected ? 'bg-white/[0.06] border border-white/10' : 'bg-transparent border border-transparent hover:bg-white/[0.03]'}"
                    >
                        <div class="w-3.5 h-3.5 rounded-sm border flex items-center justify-center shrink-0 transition-all
                            {isSelected ? 'border-white/40 bg-white/20' : 'border-white/15 bg-transparent'}">
                            {#if isSelected}
                                <svg class="w-2.5 h-2.5 text-white/80" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                </svg>
                            {/if}
                        </div>

                        <span class="{INDEX_FLAGS[key] || ''} fis rounded-sm" style="font-size: 1.1rem;"></span>
                        <div class="flex-1 text-left">
                            <div class="text-[13px] font-bold text-white/70">{cfg.shortLabel}</div>
                        </div>

                        {#if asset}
                            <div class="text-right">
                                <div class="text-[13px] font-mono font-bold text-white/60 tabular-nums">
                                    {(asset.last_price ?? 0).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}
                                </div>
                                <div class="text-[11px] font-bold tabular-nums {(asset.daily_change_pct ?? 0) >= 0 ? 'text-green-500/70' : 'text-red-500/70'}">
                                    {(asset.daily_change_pct ?? 0) >= 0 ? '▲' : '▼'}
                                    {Math.abs(asset.daily_change_pct ?? 0).toFixed(2)}%
                                </div>
                            </div>
                        {/if}
                    </button>
                {/each}
            </div>
        {:else if loading}
            <div class="flex flex-col items-center justify-center h-40 space-y-3 opacity-30">
                <div class="w-6 h-6 border-2 border-bloom-accent border-t-transparent rounded-full animate-spin"></div>
                <span class="text-[10px] font-black uppercase tracking-widest text-white">Loading Tape</span>
            </div>
        {:else if error}
            <div class="flex flex-col items-center justify-center h-40 space-y-3 px-6">
                <div class="text-center space-y-2">
                    <div class="text-red-500 text-sm font-bold">Failed to load market data</div>
                    <div class="text-bloom-text/40 text-xs font-mono">{error}</div>
                </div>
                <button onclick={() => loadSummaryData(currentIndex)} class="px-4 py-2 bg-bloom-accent/20 hover:bg-bloom-accent/30 border border-bloom-accent/50 rounded-lg text-white text-xs font-bold uppercase tracking-wider transition-all">Retry</button>
            </div>
        {:else if searchQuery}
            {#each sectorTree.flatMap(s => s.industries.flatMap(i => i.stocks)) as item}
                {@render stockRow(item)}
            {/each}
        {:else}
            <!-- stock browsing: collapsible sector > industry > stock tree -->
            {#each sectorTree as sector}
                <button onclick={() => toggleSector(sector.name)}
                    class="w-full flex items-center justify-between px-4 py-3 bg-[#1a1a24] hover:bg-[#1e1e2a] border-b border-white/[0.06] border-l-[3px] border-l-bloom-accent/50 transition-all sticky top-0 z-10"
                >
                    <div class="flex items-center gap-2.5">
                        <svg class="w-3 h-3 text-white/40 transition-transform {openSectors.has(sector.name) ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                        </svg>
                        <span class="text-[11px] font-black text-white/80 uppercase tracking-widest">{sector.name}</span>
                    </div>
                    <span class="text-[12px] font-bold text-white/25 tabular-nums">{sector.stockCount}</span>
                </button>

                {#if openSectors.has(sector.name)}
                    {#each sector.industries as industry}
                        <div class="flex items-center gap-2 px-4 py-[7px] ml-2 border-l-[2px] border-l-amber-500/30 bg-[#13131a]">
                            <span class="text-[10px] font-semibold text-white/55 uppercase tracking-wide">{industry.name}</span>
                        </div>
                        {#each industry.stocks as item}
                            {@render stockRow(item)}
                        {/each}
                    {/each}
                {/if}
            {/each}
        {/if}
    </div>
</aside>

{#snippet stockRow(item)}
    <button
        data-symbol={item.symbol}
        onclick={() => selectTicker(item.symbol)}
        title="{item.symbol}{item.name ? ' — ' + item.name : ''}"
        class="w-full pl-6 pr-3 py-2.5 flex items-center border-b border-white/[0.03] hover:bg-white/[0.04] transition-all relative overflow-hidden group
        {$selectedSymbol === item.symbol ? 'bg-orange-500/20 !border-b-orange-500/50' : ''}"
    >
        {#if $selectedSymbol === item.symbol}
            <div class="absolute left-0 top-0 bottom-0 w-1 bg-orange-500 shadow-[0_0_15px_rgba(249,115,22,0.5)]"></div>
        {/if}
        <div class="w-[28%] text-left overflow-hidden pl-1">
            <div class="font-bold text-white/80 text-sm tracking-tight group-hover:text-bloom-accent transition-colors {$selectedSymbol === item.symbol ? '!text-white/80' : ''}">{item.symbol}</div>
            <div class="text-[10px] font-medium text-white/50 uppercase tracking-wide truncate pr-1">{item.name || 'Equity'}</div>
        </div>
        <div class="w-[26%] text-right pr-2">
            <div class="text-[14px] font-mono font-bold text-white/80 leading-tight {$selectedSymbol === item.symbol ? '!text-white/70' : ''}">
                {ccy}{(item.last_price ?? 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
            </div>
            <div class="text-[12px] font-bold flex items-center justify-end gap-1 {(item.daily_change_pct ?? 0) >= 0 ? 'text-green-500/80' : 'text-red-500/80'}">
                <span>{(item.daily_change_pct ?? 0) >= 0 ? '▲' : '▼'}</span>
                <span>{Math.abs(item.daily_change_pct ?? 0).toFixed(2)}%</span>
            </div>
        </div>
        <div class="w-[28%] text-right font-mono text-[13px] leading-tight space-y-0.5">
            <div class="flex justify-end gap-1.5 text-white/20">
                <span class="font-bold">H</span>
                <span class="text-white/60 font-bold">{(item.high ?? 0).toFixed(2)}</span>
            </div>
            <div class="flex justify-end gap-1.5 text-white/20">
                <span class="font-bold">L</span>
                <span class="text-white/60 font-bold">{(item.low ?? 0).toFixed(2)}</span>
            </div>
        </div>
        <div class="w-[18%] text-right">
            <div class="text-[10px] font-bold text-white/30 uppercase tracking-tighter">Vol</div>
            <div class="text-[12px] font-bold text-white/50">{formatVol(item.volume)}</div>
        </div>
    </button>
{/snippet}

<style>
    .custom-scrollbar::-webkit-scrollbar { width: 8px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: rgba(255,255,255,0.03); border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(168,85,247,0.35); border-radius: 10px; min-height: 40px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(168,85,247,0.55); }
</style>