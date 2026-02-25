<!--
  SectionHeader — Title + subtitle + action slot
  Usage:
    <SectionHeader title="Market Overview" subtitle="Real-time data">
      {#snippet action()}<button>View All</button>{/snippet}
    </SectionHeader>
-->
<script>
    let {
        title = '',
        subtitle = '',
        subtitleColor = '',
        subtitleFlag = '',
        size = 'md',
        border = false,
        action = null,
        titleClass = '',
        level = 3,
        class: className = '',
    } = $props();

    const sizeClasses = {
        sm: { title: 'text-[11px]', subtitle: 'text-[10px]' },
        md: { title: 'text-[12px]', subtitle: 'text-[11px]' },
        lg: { title: 'text-[14px]', subtitle: 'text-[12px]' },
    };

    let sizes = $derived(sizeClasses[size] || sizeClasses.md);
</script>

<div class="section-header {border ? 'section-header-border' : ''} {className}">
    <div class="section-header-text">
        <h3 class="{sizes.title} font-semibold uppercase tracking-[0.08em] {titleClass || 'text-muted'}" aria-level={level}>
            {title}
        </h3>
        {#if subtitle}
            <span class="{sizes.subtitle} font-medium" style="color: {subtitleColor || 'var(--text-disabled)'}">
                {#if subtitleFlag}<span class="{subtitleFlag} fis rounded-sm" style="font-size: 0.85em; vertical-align: baseline; margin-right: 0.3em;"></span>{/if}{subtitle}
            </span>
        {/if}
    </div>
    {#if action}
        <div class="section-header-action">
            {@render action()}
        </div>
    {/if}
</div>

<style>
    .section-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--space-sm);
    }

    .section-header-text {
        display: flex;
        align-items: baseline;
        gap: var(--space-sm);
        min-width: 0;
    }

    .section-header-action {
        flex-shrink: 0;
    }

    .section-header-border {
        padding-bottom: var(--space-md);
        margin-bottom: var(--space-md);
        border-bottom: 1px solid var(--border-subtle);
    }
</style>
