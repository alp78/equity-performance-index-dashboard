<script>
    // --- IMPORTS ---
    import { PUBLIC_BACKEND_URL } from '$env/static/public';
    import { onMount } from 'svelte';

    // --- PROPS ---
    // Receives the selected time period (e.g., '1y', '3mo') from the parent page.
    let { currentPeriod = '1y' } = $props();
    
    // --- STATE ---
    let rankings = $state(null); // The data currently being displayed
    
    // CACHE: Stores previously fetched results in memory.
    // Structure: { '1y': { top: [...], bottom: [...] }, '3mo': ... }
    // If user clicks '1Y' -> '3M' -> '1Y', the second '1Y' is instant (no network call).
    let rankingCache = {}; 
    
    // ABORT CONTROLLER: Manages network requests.
    // If user fast-clicks '1Y' -> '3M' -> 'MAX', we must cancel the '1Y' and '3M' requests
    // so they don't overwrite the correct 'MAX' data when they finally arrive.
    let abortController = null;

    // --- DATA LOADING LOGIC ---
    async function load(period) {
        // 1. CACHE HIT CHECK
        // If we already have this data in memory, use it immediately.
        if (rankingCache[period]) {
            rankings = rankingCache[period];
            return;
        }

        // 2. CANCEL PREVIOUS REQUESTS
        // If a request is currently flying, kill it.
        if (abortController) {
            abortController.abort();
        }
        // Create a new controller for this specific request
        abortController = new AbortController();
        const signal = abortController.signal;

        // 3. UI RESET
        // Set to null to show the loading spinner while fetching
        rankings = null;

        try {
            // Add timestamp (?t=...) to prevent browser-level HTTP caching issues if needed
            const url = `${PUBLIC_BACKEND_URL}/rankings?period=${period}&t=${Date.now()}`;
            const res = await fetch(url, { signal });
            
            if (res.ok) {
                const data = await res.json();
                
                // 4. WRITE TO CACHE
                rankingCache[period] = data;
                
                // 5. UPDATE UI
                rankings = data;
            }
        } catch (e) { 
            // Ignore "AbortError" because that means *we* cancelled it on purpose.
            if (e.name !== 'AbortError') {
                console.error("Ranking Load Error:", e); 
            }
        }
    }

    // --- REACTIVITY ---
    // Whenever 'currentPeriod' changes (user clicks a button), run the load function.
    $effect(() => { 
        load(currentPeriod); 
    });

    // --- BACKGROUND PREFETCHING ---
    // UX Trick: When the component mounts, we silently fetch ALL other periods in the background.
    // This ensures that when the user eventually clicks them, the data is already in the cache.
    onMount(() => {
        const others = ['1w', '1mo', '3mo', '6mo', '1y', '5y', 'max'];
        others.forEach(p => {
            // Only fetch if not current and not already cached
            if (p !== currentPeriod && !rankingCache[p]) {
                fetch(`${PUBLIC_BACKEND_URL}/rankings?period=${p}`)
                    .then(res => res.json())
                    .then(data => { rankingCache[p] = data; })
                    .catch(() => {}); // Silent fail is fine here
            }
        });
    });

    // --- VISUALIZATION HELPER ---
    // Finds the maximum absolute value in a list to scale the bar charts correctly.
    // Example: If top gainer is +50%, that bar should take up 100% of the available width.
    function getSubsetMax(items) {
        if (!items || items.length === 0) return 1;
        return Math.max(...items.map(i => Math.abs(i.value || 0)), 1);
    }
</script>

<div class="h-full w-full flex flex-col bg-white/5 rounded-3xl p-4 border border-white/5 overflow-hidden shadow-2xl backdrop-blur-md">
    <div class="flex-none flex justify-center items-center mb-2 border-b border-white/5 pb-2">
        <h3 class="text-[9px] font-black text-white/30 uppercase tracking-[0.3em]">
            Period Performance ({currentPeriod.toUpperCase()})
        </h3>
    </div>

    <div class="flex-1 flex flex-col min-h-0 gap-1 overflow-hidden">
        {#if rankings && rankings.selected}
            {@const data = rankings.selected}
            {@const topMax = getSubsetMax(data.top)}
            {@const botMax = getSubsetMax(data.bottom)}

            <div class="flex-1 flex flex-col min-h-0 gap-1">
                
                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (data.top || []).slice(0, 3) as item}
                        {@const width = (item.value / topMax) * 75}
                        
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <span class="w-12 text-[12px] font-black text-white shrink-0 uppercase tracking-tighter mr-2">
                                {item.symbol}
                            </span>
                            
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative">
                                <div class="h-full bg-green-500/20 border-l-2 border-green-500 flex items-center justify-end rounded-sm relative transition-all duration-700 ease-out" 
                                     style="width: {width}%">
                                    <span class="text-[11px] font-medium text-white whitespace-nowrap px-2 {width < 45 ? 'absolute left-full ml-1' : ''}">
                                        +{item.value.toFixed(1)}%
                                    </span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>

                <div class="flex-none h-px bg-white/10 mx-2"></div>

                <div class="flex-1 flex flex-col justify-around py-1">
                    {#each (data.bottom || []).slice(0, 3) as item}
                        {@const width = (Math.abs(item.value) / botMax) * 75}
                        
                        <div class="flex items-center w-full gap-2 flex-1 min-h-0">
                            <span class="w-12 text-[12px] font-black text-white shrink-0 uppercase tracking-tighter mr-2">
                                {item.symbol}
                            </span>
                            
                            <div class="flex-1 h-3/5 rounded-sm overflow-hidden relative flex justify-end">
                                <div class="h-full bg-red-500/20 border-r-2 border-red-500 flex items-center justify-start rounded-sm relative transition-all duration-700 ease-out" 
                                     style="width: {width}%">
                                    <span class="text-[11px] font-medium text-white whitespace-nowrap px-2 {width < 45 ? 'absolute right-full mr-1' : ''}">
                                        {item.value.toFixed(1)}%
                                    </span>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        {:else}
            <div class="flex-1 flex items-center justify-center">
                <div class="w-4 h-4 border border-white/10 border-t-white/40 rounded-full animate-spin"></div>
            </div>
        {/if}
    </div>
</div>

<style>
    div { user-select: none; }
    
    /* Smooth animation for the bars growing/shrinking */
    .transition-all {
        transition-property: all;
        transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
</style>