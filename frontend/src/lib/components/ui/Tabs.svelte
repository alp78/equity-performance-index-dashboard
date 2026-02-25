<!--
  Tabs — Animated tab switcher with indicator bar
  Usage:
    <Tabs items={['Overview','Details','Charts']} bind:active={currentTab} />
-->
<script>
    let {
        items = [],
        active = '',
        size = 'sm',
        onchange = null,
        class: className = '',
    } = $props();

    function select(item) {
        active = item;
        onchange?.(item);
    }

    const sizeClasses = {
        xs: 'text-[10px] px-2 py-1',
        sm: 'text-[11px] px-3 py-1.5',
        md: 'text-[12px] px-4 py-2',
    };
</script>

<div class="tabs-root {className}" role="tablist">
    {#each items as item}
        {@const isActive = item === active}
        <button
            role="tab"
            aria-selected={isActive}
            class="tab-item {sizeClasses[size] || sizeClasses.sm}"
            class:tab-active={isActive}
            onclick={() => select(item)}
        >
            {item}
        </button>
    {/each}
</div>

<style>
    .tabs-root {
        display: flex;
        align-items: center;
        gap: var(--space-2xs);
        background: var(--surface-2);
        border-radius: var(--radius-md);
        padding: var(--space-2xs);
    }

    .tab-item {
        position: relative;
        font-family: var(--font-sans);
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-muted);
        border: none;
        background: transparent;
        border-radius: var(--radius-sm);
        cursor: pointer;
        transition: all var(--transition-default);
        white-space: nowrap;
    }

    .tab-item:hover {
        color: var(--text-secondary);
        background: var(--hover-overlay);
    }

    .tab-active {
        color: var(--text-primary);
        background: var(--surface-1);
        box-shadow: var(--shadow-sm);
        animation: tab-slide var(--transition-default) ease-out;
    }

    .tab-active:hover {
        background: var(--surface-1);
    }
</style>
