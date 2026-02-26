<!--
  Stat — Key metric display with value, label, and optional change
-->
<script>
    import { untrack } from 'svelte';

    let {
        label = '',
        value = '',
        change = null,
        changeLabel = '',
        size = 'md',
        class: className = '',
    } = $props();

    let flashClass = $state('');
    let prevValue = $state('');

    $effect(() => {
        const v = value;
        const pv = untrack(() => prevValue);
        if (pv && v !== pv) {
            const c = untrack(() => change);
            flashClass = c != null && c < 0 ? 'flash-down' : 'flash-up';
            const t = setTimeout(() => { flashClass = ''; }, 700);
            prevValue = v;
            return () => clearTimeout(t);
        }
        prevValue = v;
    });

    let changeColor = $derived(
        change === null ? '' :
        change > 0 ? 'text-positive' :
        change < 0 ? 'text-negative' :
        'text-muted'
    );
    let changePrefix = $derived(change !== null && change > 0 ? '+' : '');
    let changeSymbol = $derived(
        change === null ? '' :
        change > 0 ? '▲ ' :
        change < 0 ? '▼ ' : ''
    );

    const sizeClasses = {
        sm: { value: 'text-lg', label: 'text-[11px]', change: 'text-[11px]' },
        md: { value: 'text-2xl', label: 'text-[12px]', change: 'text-[12px]' },
        lg: { value: 'text-3xl', label: 'text-[13px]', change: 'text-[13px]' },
    };

    let sizes = $derived(sizeClasses[size] || sizeClasses.md);
</script>

<div class="stat-root flex flex-col gap-1 {className}">
    {#if label}
        <span class="{sizes.label} font-medium uppercase tracking-[0.05em] text-muted">
            {label}
        </span>
    {/if}
    <div class="flex items-baseline gap-2 rounded px-1 -mx-1 {flashClass}">
        <span class="{sizes.value} font-bold text-primary tabular-nums">
            {value}
        </span>
        {#if change !== null}
            <span class="{sizes.change} font-semibold tabular-nums {changeColor}">
                {changeSymbol}{changePrefix}{typeof change === 'number' ? change.toFixed(2) : change}{changeLabel || '%'}
            </span>
        {/if}
    </div>
</div>

<style>
    .tabular-nums { font-variant-numeric: tabular-nums; }
</style>
