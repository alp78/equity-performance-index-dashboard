<script>
    // --- IMPORTS ---
    import { onMount, onDestroy } from 'svelte';
    import { PUBLIC_BACKEND_URL } from '$env/static/public';

    // --- PROPS ---
    // Flexible component: Can display US Equities, Macro, Crypto, etc.
    // 'symbols' is an array of strings like ['NVDA', 'BINANCE:BTCUSDT']
    let { title = "INDICATORS", symbols = [] } = $props();
    
    // --- STATE ---
    // 'quotes' is a dictionary mapping Symbol -> { price, pct, diff }
    // We use a dictionary for O(1) lookups when WebSocket messages arrive.
    let quotes = $state({});
    let socket; // WebSocket instance reference

    // --- HELPERS ---

    // Removes prefixes like "BINANCE:" or "FXCM:" for cleaner UI display.
    function clean(s) {
        return s && s.includes(':') ? s.split(':')[1] : s;
    }

    // Logic: Determine if the market is Open or Closed based on the asset type.
    // This controls the "Red/Green" dot and the "Pulsing" animation.
    function getStatus(symbol) {
        const s = symbol.toUpperCase();
        
        // 1. Crypto: Always Live (24/7)
        if (s.includes('BTC') || s.includes('ETH')) {
            return { label: 'LIVE', color: 'text-green-500', dot: 'bg-green-500', pulse: true };
        }

        // 2. Traditional Markets (Stocks/Forex): Closed on Weekends
        const now = new Date();
        const day = now.getUTCDay(); // 0 = Sunday, 6 = Saturday
        // Note: Forex technically opens Sunday evening, but for UI simplicity we mark Sunday as Closed.
        if (day === 0 || day === 6) {
            return { label: 'CLOSED', color: 'text-red-500', dot: 'bg-red-500', pulse: false };
        }

        // 3. Weekdays: Assume Live
        return { label: 'LIVE', color: 'text-green-500', dot: 'bg-green-500', pulse: true };
    }

    // --- LIFECYCLE: CONNECT ---
    onMount(() => {
        // 1. Convert HTTP URL to WebSocket URL (https:// -> wss://)
        const wsUrl = PUBLIC_BACKEND_URL.replace('https://', 'wss://') + '/ws/market';
        
        // 2. Initialize placeholders to prevent "undefined" errors in the UI
        symbols.forEach(s => { quotes[s] = { price: 0, pct: 0, diff: 0 }; });

        // 3. Open Connection
        socket = new WebSocket(wsUrl);

        socket.onmessage = (event) => {
            try {
                const update = JSON.parse(event.data);
                
                // 4. Update State efficiently
                // We loop through our props to see if the incoming message belongs to this panel.
                for (const s of symbols) {
                    // Match either full symbol ("BINANCE:BTCUSDT") or clean symbol ("BTCUSDT")
                    if (s === update.symbol || clean(s) === update.symbol) {
                        quotes[s] = {
                            price: update.price,
                            pct: update.pct,
                            diff: update.diff
                        };
                        break; // Stop looking once found
                    }
                }
            } catch (e) { console.error("Payload Error:", e); }
        };
    });

    // --- LIFECYCLE: CLEANUP ---
    // Critical: Close the socket when navigating away to prevent memory leaks.
    onDestroy(() => socket?.close());
</script>

<div class="flex flex-col h-full bg-white/5 rounded-3xl p-5 border border-white/5 shadow-2xl backdrop-blur-md overflow-hidden">
    <div class="flex justify-between items-center mb-4 border-b border-white/5 pb-3">
        <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">{title}</h3>
    </div>

    <div class="flex-1 grid grid-rows-3 gap-2">
        {#each symbols as symbol}
            {@const data = quotes[symbol] || { price: 0, pct: 0, diff: 0 }}
            {@const status = getStatus(symbol)}
            
            {@const hasData = data.price > 0}

            <div class="flex items-center justify-between group py-1">
                <div class="flex flex-col">
                    <span class="text-sm font-black text-white uppercase tracking-normal">
                        {clean(symbol)}
                    </span>
                    <div class="flex items-center gap-1.5 mt-0.5">
                        <div class="w-1.5 h-1.5 rounded-full {status.dot} {status.pulse ? 'animate-pulse' : ''}"></div>
                        <span class="text-[9px] font-bold {status.color} uppercase tracking-tighter">
                            {status.label}
                        </span>
                    </div>
                </div>

                <div class="flex flex-col items-end">
                    <span class="text-base font-mono font-black text-white leading-none mb-1 tabular-nums">
                        {#if hasData}
                            ${data.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                        {:else}
                            <span class="animate-pulse opacity-20">---</span>
                        {/if}
                    </span>
                    
                    <div class="flex items-center gap-1.5 font-bold {data.pct >= 0 ? 'text-green-500' : 'text-red-500'}">
                         {#if hasData}
                            <span class="text-sm tabular-nums">
                                {data.pct >= 0 ? '+' : ''}{data.pct.toFixed(2)}%
                            </span> 
                            <span class="text-[11px] opacity-70 tabular-nums font-black">
                                ({data.diff >= 0 ? '+' : ''}{data.diff.toFixed(2)})
                            </span>
                        {:else}
                             <span class="text-[10px] opacity-20">waiting...</span>
                        {/if}
                    </div>
                </div>
            </div>
        {/each}
    </div>
</div>