<!--
  StockMetrics — Key stats strip for stock browsing mode
  Shows price, daily change, volume, and day range at a glance.
-->
<script>
    import { selectedSymbol, summaryData, currentCurrency } from '$lib/stores.js';
    import Stat from '$lib/components/ui/Stat.svelte';
    import StatGroup from '$lib/components/ui/StatGroup.svelte';

    let asset = $derived(
        ($summaryData.assets || []).find(a => a.symbol === $selectedSymbol)
    );

    let ccy = $derived($currentCurrency);

    function fmtPrice(v) {
        if (v == null) return '---';
        return ccy + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function fmtVol(v) {
        if (!v) return '---';
        if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M';
        if (v >= 1_000) return (v / 1_000).toFixed(0) + 'K';
        return v.toString();
    }
</script>

{#if asset}
    <StatGroup columns="auto" gap="md" class="px-1">
        <Stat
            label="Price"
            value={fmtPrice(asset.last_price)}
            change={asset.daily_change_pct ?? null}
            size="sm"
        />
        <Stat
            label="Day High"
            value={fmtPrice(asset.high)}
            size="sm"
        />
        <Stat
            label="Day Low"
            value={fmtPrice(asset.low)}
            size="sm"
        />
        <Stat
            label="Volume"
            value={fmtVol(asset.volume)}
            size="sm"
        />
    </StatGroup>
{/if}
