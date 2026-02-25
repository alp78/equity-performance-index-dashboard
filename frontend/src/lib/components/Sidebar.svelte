<!--
  Sidebar — Navigation Orchestrator
  Thin wrapper that imports the nav rail and content panels,
  routing to the correct panel based on the current mode.
-->

<script>
    import { browser } from '$app/environment';
    import {
        marketIndex,
        isOverviewMode,
        isSectorMode,
        isMacroContextMode,
        INDEX_CONFIG,
        selectedSymbol,
        loadSummaryData,
        sectorAnalysisMode,
        singleSelectedIndex,
        selectedSector,
        requestFocusSymbol,
    } from '$lib/stores.js';

    import StockBrowser from './sidebar/StockBrowser.svelte';
    import SectorPanel from './sidebar/SectorPanel.svelte';
    import MacroPanel from './sidebar/MacroPanel.svelte';

    let {} = $props();

    // --- CROSS-MODE NAVIGATION ---

    // Called from StockBrowser "Sector Analysis" hover button
    async function handleGoToSectorAnalysis(sectorName) {
        const stockIndex = $marketIndex;
        try { sessionStorage.setItem('dash_last_stock_index', stockIndex); } catch {}
        marketIndex.set('sectors');
        sectorAnalysisMode.set('single-index');
        const idxKey = (stockIndex && INDEX_CONFIG[stockIndex]) ? stockIndex : 'stoxx50';
        singleSelectedIndex.set([idxKey]);
        try { sessionStorage.setItem('dash_single_open_index', idxKey); } catch {}
        selectedSector.set(sectorName);
    }

    // Called from SectorPanel "Stock Mode" hover button
    async function handleGoToStockMode(sectorName) {
        const idxKey = (browser && sessionStorage.getItem('dash_single_open_index')) || 'stoxx50';
        if (idxKey && INDEX_CONFIG[idxKey]) {
            marketIndex.set(idxKey);
            const newTickers = await loadSummaryData(idxKey);
            if (newTickers && newTickers.length > 0) {
                const firstStock = newTickers.find(t =>
                    (t.sector && t.sector !== 0 ? t.sector : 'Other') === sectorName
                );
                if (firstStock) {
                    selectedSymbol.set(firstStock.symbol);
                    // Open the sector tab and scroll to center the stock
                    requestFocusSymbol(firstStock.symbol);
                }
            }
        }
    }
</script>

<aside class="flex flex-col h-full bg-bg-sidebar border-r border-border relative z-20 overflow-hidden max-lg:h-dvh">
    {#if $isOverviewMode || $isMacroContextMode}
        <MacroPanel />
    {:else if $isSectorMode}
        <SectorPanel onGoToStockMode={handleGoToStockMode} />
    {:else}
        <StockBrowser onGoToSectorAnalysis={handleGoToSectorAnalysis} />
    {/if}
</aside>
