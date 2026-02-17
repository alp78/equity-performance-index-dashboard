<script>
    // --- IMPORTS ---
    import { PUBLIC_BACKEND_URL } from '$env/static/public';
    import { onMount } from 'svelte';
    
    // GLOBAL STORE:
    // This is the "Remote Control" of the app. When we set 'selectedSymbol' here,
    // the +page.svelte (Chart) automatically reacts and fetches new data.
    import { selectedSymbol, summaryData, loadSummaryData } from '$lib/stores.js';

    // --- STATE (Svelte 5 Runes) ---
    let searchQuery = $state('');   // User input for filtering
    let lastPriceDate = $state('—'); // Display date for the "Last Update" label

    // --- OPTIMIZATION: Read from dedicated summary store (won't change when period changes) ---
    let tickers = $derived($summaryData.assets || []);
    let loading = $derived($summaryData.loading);
    let error = $derived($summaryData.error);

    // --- DERIVED STATE (Reactive Filtering) ---
    // This automatically re-runs whenever 'tickers' OR 'searchQuery' changes.
    // It performs two operations in one pass:
    // 1. FILTER: Matches symbol (NVDA) or name (NVIDIA).
    // 2. SORT: Alphabetical order A-Z.
    let filteredTickers = $derived(
        tickers
            .filter(t => 
                t.symbol.toLowerCase().includes(searchQuery.toLowerCase()) || 
                (t.name && t.name.toLowerCase().includes(searchQuery.toLowerCase()))
            )
            .sort((a, b) => a.symbol.localeCompare(b.symbol))
    );

    // --- WATCH FOR DATA LOADED ---
    // Update the last price date when data arrives
    $effect(() => {
        if (tickers.length > 0) {
            lastPriceDate = new Date().toLocaleDateString('en-GB');
        }
    });

    // --- HELPER: FORMAT VOLUME ---
    // Converts raw numbers (15000000) into human readable strings (15.0M)
    // Critical for fitting large numbers into the sidebar columns.
    function formatVol(val) {
        if (!val) return '0';
        if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
        if (val >= 1000) return (val / 1000).toFixed(0) + 'K';
        return val.toString();
    }
</script>

<aside class="w-[480px] bg-bloom-card border-r border-bloom-muted/10 flex flex-col h-screen shrink-0 relative z-20 shadow-2xl">
    
    <div class="p-6 space-y-4 bg-gradient-to-b from-white/5 to-transparent">
        
        <div class="flex justify-between items-end">
            <div>
                <h2 class="text-xs font-black text-bloom-accent uppercase tracking-[0.3em] mb-1">EQUITY PERFORMANCE INDEX</h2>
                <div class="text-3xl font-black text-white tracking-tighter uppercase">S&P 500</div>
            </div>
            <div class="text-right">
                <div class="text-[10px] font-bold text-bloom-text/40 uppercase tracking-widest">Last Update</div>
                <div class="text-xs font-mono font-bold text-bloom-accent">{lastPriceDate}</div>
            </div>
        </div>

        <div class="relative group">
            <input 
                type="text" 
                bind:value={searchQuery}
                placeholder="Search symbol or name..." 
                class="w-full bg-black/40 border border-bloom-muted/20 rounded-xl py-3 px-4 pl-10 text-sm font-bold text-white placeholder:text-bloom-text/20 focus:outline-none focus:border-bloom-accent/50 focus:ring-4 focus:ring-bloom-accent/10 transition-all"
            />
            <svg class="absolute left-3 top-3.5 w-4 h-4 text-bloom-text/20 group-focus-within:text-bloom-accent transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
        </div>
    </div>

    <div class="flex-1 overflow-y-auto custom-scrollbar">
        {#if loading}
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
                <button
                    onclick={() => loadSummaryData(PUBLIC_BACKEND_URL)}
                    class="px-4 py-2 bg-bloom-accent/20 hover:bg-bloom-accent/30 border border-bloom-accent/50 rounded-lg text-white text-xs font-bold uppercase tracking-wider transition-all"
                >
                    Retry
                </button>
            </div>
        {:else if filteredTickers.length === 0}
            <div class="flex flex-col items-center justify-center h-40 space-y-3 opacity-30">
                <span class="text-[10px] font-black uppercase tracking-widest text-white">No matches for "{searchQuery}"</span>
            </div>
        {:else}
            {#each filteredTickers as item}
                <button 
                    onclick={() => selectedSymbol.set(item.symbol)}
                    class="w-full px-6 py-5 flex items-center border-b border-white/5 hover:bg-white/5 transition-all relative overflow-hidden group
                    {$selectedSymbol === item.symbol ? 'bg-bloom-accent/10' : ''}"
                >
                    {#if $selectedSymbol === item.symbol}
                        <div class="absolute left-0 top-0 bottom-0 w-1 bg-bloom-accent shadow-[0_0_15px_rgba(168,85,247,0.5)]"></div>
                    {/if}

                    <div 
                        class="w-[35%] text-left overflow-hidden" 
                        title="{item.symbol} - {item.name || 'Equity'}"
                    >
                        <div class="font-black text-white text-base tracking-tight group-hover:text-bloom-accent transition-colors">
                            {item.symbol}
                        </div>
                        <div class="text-[9px] font-bold text-bloom-text/30 uppercase tracking-widest truncate pr-2">
                            {item.name || 'Equity'}
                        </div>
                    </div>
                    
                    <div class="w-[25%] text-right pr-4">
                        <div class="text-sm font-mono font-black text-white leading-tight">
                            ${(item.last_price ?? 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                        </div>
                        <div class="text-[10px] font-bold flex items-center justify-end gap-1 {(item.daily_change_pct ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'}">
                            <span>{(item.daily_change_pct ?? 0) >= 0 ? '▲' : '▼'}</span>
                            <span>{Math.abs(item.daily_change_pct ?? 0).toFixed(2)}%</span>
                        </div>
                    </div>

                    <div class="w-[25%] text-right font-mono text-[10px] leading-tight space-y-1">
                        <div class="flex justify-end gap-2 text-white/40">
                            <span class="font-bold">H</span>
                            <span class="text-white font-bold">{(item.high ?? 0).toFixed(2)}</span>
                        </div>
                        <div class="flex justify-end gap-2 text-white/40">
                            <span class="font-bold">L</span>
                            <span class="text-white font-bold">{(item.low ?? 0).toFixed(2)}</span>
                        </div>
                    </div>

                    <div class="w-[15%] text-right">
                        <div class="text-[9px] font-black text-bloom-text/30 uppercase tracking-tighter">Vol</div>
                        <div class="text-[10px] font-bold text-white/60">{formatVol(item.volume)}</div>
                    </div>
                </button>
            {/each}
        {/if}
    </div>
</aside>

<style>
    /* CUSTOM SCROLLBAR STYLING (Webkit only) */
    .custom-scrollbar::-webkit-scrollbar { width: 4px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.05); border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(168,85,247,0.3); }
</style>