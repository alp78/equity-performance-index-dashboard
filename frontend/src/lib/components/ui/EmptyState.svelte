<!--
  EmptyState — Data unavailable placeholder
  Usage:
    <EmptyState icon={AlertCircle} title="No data" message="Try adjusting your filters" />
-->
<script>
    let {
        icon = null,
        title = 'No data available',
        message = '',
        class: className = '',
        children = null,
    } = $props();
</script>

<div class="empty-state {className}">
    {#if icon}
        <div class="empty-icon">
            <svelte:component this={icon} size={32} strokeWidth={1.5} />
        </div>
    {/if}
    <span class="empty-title">{title}</span>
    {#if message}
        <span class="empty-message">{message}</span>
    {/if}
    {#if children}
        <div class="empty-action">
            {@render children()}
        </div>
    {/if}
</div>

<style>
    .empty-state {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: var(--space-sm);
        padding: var(--space-3xl) var(--space-xl);
        text-align: center;
    }

    .empty-icon {
        color: var(--text-disabled);
        margin-bottom: var(--space-xs);
    }

    .empty-title {
        font-size: var(--text-sm);
        font-weight: 500;
        color: var(--text-muted);
    }

    .empty-message {
        font-size: var(--text-xs);
        color: var(--text-disabled);
        max-width: 280px;
    }

    .empty-action {
        margin-top: var(--space-sm);
    }
</style>
