<!--
  MacroPanel — Index cards for Global Macro mode
  Each card shows: index price, FX rate, exchange name, city, local time
-->
<script>
    import { onMount, onDestroy } from 'svelte';
    import { indexOverviewData, macroHighlightIndex, macroHighlightPair, INDEX_GROUPS } from '$lib/stores.js';
    import { getCached, setCached, isCacheFresh } from '$lib/cache.js';
    import { INDEX_COLORS, INDEX_EXCHANGE_INFO, INDEX_CONFIG, INDEX_KEY_TO_TICKER, fmtFx } from '$lib/index-registry.js';
    import { API_BASE_URL } from '$lib/config.js';

    let {} = $props();

    let overviewAssets = $derived($indexOverviewData.assets || []);

    // ── FX Rates ──
    const FX_TTL = 5 * 60 * 1000;
    let fxRates = $state({});
    let fxLoading = $state(true);
    let fxTimer;

    async function loadFx() {
        try {
            const res = await fetch(`${API_BASE_URL}/macro/fx`);
            if (res.ok) {
                const d = await res.json();
                fxRates = d.rates || {};
                setCached('macro_fx', fxRates, FX_TTL);
            }
        } catch {}
        fxLoading = false;
    }

    // fmtFx imported from $lib/index-registry.js

    // ── Local time ──
    let now = $state(new Date());
    let clockTimer;

    function localTime(tz) {
        try {
            return now.toLocaleTimeString('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false });
        } catch { return '—'; }
    }

    // ── Lifecycle ──
    onMount(() => {
        const cached = getCached('macro_fx');
        if (cached) { fxRates = cached.data; fxLoading = false; }
        if (!isCacheFresh('macro_fx')) loadFx();
        fxTimer = setInterval(loadFx, FX_TTL);
        clockTimer = setInterval(() => { now = new Date(); }, 60_000);
    });

    onDestroy(() => {
        if (fxTimer) clearInterval(fxTimer);
        if (clockTimer) clearInterval(clockTimer);
    });
</script>

<div class="flex flex-col h-full overflow-hidden">
    <!-- index cards -->
    <div class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar">
    {#each INDEX_GROUPS as group}
        <div class="px-4 py-2 text-[11px] font-semibold text-text-faint uppercase tracking-widest border-b border-border-subtle">{group.label}</div>
        {#each group.indices as idx}
            {@const key = idx.key}
            {@const cfg = INDEX_CONFIG[key]}
            {@const ticker = INDEX_KEY_TO_TICKER[key]}
            {@const asset = overviewAssets.find(a => a.symbol === ticker)}
            {@const info = INDEX_EXCHANGE_INFO[key]}
            {@const fxPair = info?.fxPair}
            {@const fxRaw = fxPair ? fxRates[fxPair] : null}
            {@const fxVal = fxRaw != null ? (typeof fxRaw === 'object' ? fxRaw.value : fxRaw) : null}
            {@const fxPct = fxRaw != null && typeof fxRaw === 'object' ? fxRaw.changePct : null}
            {@const isHighlighted = $macroHighlightIndex === key || $macroHighlightPair?.row === key || $macroHighlightPair?.col === key}
            {@const exchangeName = asset?.exchange || ''}

            <button
                class="w-full text-left px-4 py-2.5 border-b border-border-subtle hover:bg-bg-hover transition-colors cursor-pointer"
                style={isHighlighted ? `background:${INDEX_COLORS[key] || 'transparent'}20` : ''}
                onclick={() => macroHighlightIndex.set(isHighlighted ? null : key)}
            >
                <!-- Row 1: Flag + Index name + Price + change -->
                <div class="flex items-center gap-2.5 mb-1">
                    <span class="{cfg.flag} fis rounded-sm shrink-0" style="font-size: 1.2rem;"></span>
                    <span class="text-[15px] font-bold truncate transition-colors"
                          style="color:{isHighlighted ? (INDEX_COLORS[key] || 'var(--text-secondary)') : 'var(--text-secondary)'}">{cfg.shortLabel}</span>
                    {#if asset}
                        <span class="ml-auto text-[length:var(--text-num-xl)] font-semibold tabular-nums">
                            <span class="text-text-muted">{cfg.currency}</span><span class="text-text">{(asset.last_price ?? 0).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}</span>
                        </span>
                        <span class="text-[length:var(--text-num-lg)] font-semibold tabular-nums" style="color: var({(asset.daily_change_pct ?? 0) >= 0 ? '--color-positive' : '--color-negative'})">
                            {(asset.daily_change_pct ?? 0) >= 0 ? '+' : ''}{(asset.daily_change_pct ?? 0).toFixed(2)}%
                        </span>
                    {:else}
                        <span class="ml-auto flex items-center gap-1.5">
                            <div class="w-2.5 h-2.5 border border-border border-t-text-muted rounded-full animate-spin"></div>
                        </span>
                    {/if}
                </div>

                <!-- Row 2: FX rate (if applicable) -->
                {#if fxPair}
                    <div class="flex items-baseline gap-2 mb-0.5 pl-[1.7rem]">
                        <span class="text-[13px] font-medium text-text-muted">
                            {fxPair}
                            {#if fxVal != null}
                                <span class="tabular-nums text-text-secondary ml-1">{fmtFx(fxPair, fxVal)}</span>
                            {/if}
                        </span>
                        {#if fxPct != null}
                            <span class="text-[length:var(--text-num-sm)] font-medium tabular-nums" style="color: var({fxPct >= 0 ? '--color-positive' : '--color-negative'})">
                                {fxPct >= 0 ? '+' : ''}{Math.abs(fxPct).toFixed(2)}%
                            </span>
                        {/if}
                    </div>
                {/if}

                <!-- Row 3: Exchange · City · Local time -->
                <div class="flex items-center gap-1.5 text-[12px] text-text-faint uppercase tracking-wider pl-[1.7rem]">
                    {#if exchangeName}
                        <span>{exchangeName}</span>
                        <span class="text-border">·</span>
                    {/if}
                    <span>{info?.city || ''}</span>
                    <span class="text-border">·</span>
                    <span class="tabular-nums">{localTime(info?.tz)}</span>
                </div>
            </button>
        {/each}
    {/each}
    </div>
</div>

<style>
    .custom-scrollbar::-webkit-scrollbar { width: 6px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: transparent; border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 10px; min-height: 40px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }
</style>
