<script>
    import { onMount, onDestroy } from 'svelte';
    let { title = "INDICATORS", symbols = [] } = $props();
    
    let quotes = $state({});
    let connectionStatus = $state("Offline"); 
    let socket;

    function clean(s) {
        return s && s.includes(':') ? s.split(':')[1] : s;
    }
    import { PUBLIC_BACKEND_URL } from '$env/static/public';

    onMount(() => {
        const wsUrl = PUBLIC_BACKEND_URL.replace('https://', 'wss://') + '/ws/market';
        symbols.forEach(s => { quotes[s] = { price: 0, pct: 0, diff: 0 }; });

        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            connectionStatus = "Live";
            console.log(`${title} Connected`);
        };

        socket.onmessage = (event) => {
            try {
                const update = JSON.parse(event.data);
                for (const s of symbols) {
                    if (s === update.symbol || clean(s) === update.symbol) {
                        quotes[s] = {
                            price: update.price,
                            pct: update.pct,
                            diff: update.diff
                        };
                        break; 
                    }
                }
            } catch (e) { console.error("Payload Error:", e); }
        };

        socket.onclose = () => { connectionStatus = "Offline"; };
    });

    onDestroy(() => socket?.close());
</script>

<div class="flex flex-col h-full bg-white/5 rounded-3xl p-5 border border-white/5 shadow-2xl backdrop-blur-md overflow-hidden">
    <div class="flex justify-between items-center mb-4 border-b border-white/5 pb-3">
        <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">{title}</h3>
        <div class="flex items-center gap-1.5">
            <div class="w-1.5 h-1.5 {connectionStatus === 'Live' ? 'bg-green-500' : 'bg-red-500'} rounded-full animate-pulse"></div>
            <span class="text-[9px] font-black {connectionStatus === 'Live' ? 'text-green-500' : 'text-red-500'} uppercase tracking-widest">
                {connectionStatus}
            </span>
        </div>
    </div>

    <div class="flex-1 grid grid-rows-3 gap-2">
        {#each symbols as symbol}
            {@const data = quotes[symbol] || { price: 0, pct: 0, diff: 0 }}
            <div class="flex items-center justify-between group py-1">
                <div class="flex flex-col">
                    <span class="text-sm font-black text-white uppercase tracking-normal">
                        {clean(symbol)}
                    </span>
                    <span class="text-[9px] font-bold text-white/20 uppercase tracking-tighter">Market Open</span>
                </div>

                <div class="flex flex-col items-end">
                    <span class="text-base font-mono font-black text-white leading-none mb-1 tabular-nums">
                        {#if data.price > 0}
                            ${data.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                        {:else}
                            <span class="animate-pulse opacity-20">---</span>
                        {/if}
                    </span>
                    
                    <div class="flex items-center gap-1.5 font-bold {data.pct >= 0 ? 'text-green-500' : 'text-red-500'}">
                        <span class="text-sm tabular-nums">
                            {data.pct >= 0 ? '+' : ''}{data.pct.toFixed(2)}%
                        </span> 
                        <span class="text-[11px] opacity-70 tabular-nums font-black">
                            ({data.diff >= 0 ? '+' : ''}{data.diff.toFixed(2)})
                        </span>
                    </div>
                </div>
            </div>
        {/each}
    </div>
</div>