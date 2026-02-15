<script>
    import { PUBLIC_BACKEND_URL } from '$env/static/public';
    import { onMount } from 'svelte';

    let { currentPeriod = '1y' } = $props();
    
    // STATE & CACHE
    let rankings = $state(null);
    let rankingCache = {}; // Local memory cache
    let abortController = null; // To kill stale requests

    async function load(period) {
        // if in cache, use it immediately.
        if (rankingCache[period]) {
            rankings = rankingCache[period];
            return;
        }

        // Cancel any previous pending request
        if (abortController) {
            abortController.abort();
        }
        abortController = new AbortController();
        const signal = abortController.signal;

        // Reset UI to spinner while loading new data
        rankings = null;

        try {
            const url = `${PUBLIC_BACKEND_URL}/rankings?period=${period}&t=${Date.now()}`;
            const res = await fetch(url, { signal });
            
            if (res.ok) {
                const data = await res.json();
                // Store in cache for next time
                rankingCache[period] = data;
                rankings = data;
            }
        } catch (e) { 
            if (e.name !== 'AbortError') {
                console.error("Ranking Load Error:", e); 
            }
        }
    }

    // TRIGGER: Reload when period changes
    $effect(() => { 
        load(currentPeriod); 
    });

    // BACKGROUND PREFETCH: Silently load other periods so they are ready before click
    onMount(() => {
        const others = ['1w', '1mo', '3mo', '6mo', '1y', '5y', 'max'];
        others.forEach(p => {
            if (p !== currentPeriod && !rankingCache[p]) {
                fetch(`${PUBLIC_BACKEND_URL}/rankings?period=${p}`)
                    .then(res => res.json())
                    .then(data => { rankingCache[p] = data; })
                    .catch(() => {});
            }
        });
    });

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
    
    .transition-all {
        transition-property: all;
        transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
</style>