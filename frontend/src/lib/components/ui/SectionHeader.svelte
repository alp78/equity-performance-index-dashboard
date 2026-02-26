<!--
  SectionHeader — Title + subtitle + action slot
  Usage:
    <SectionHeader title="Market Overview" subtitle="Real-time data">
      {#snippet action()}<button>View All</button>{/snippet}
    </SectionHeader>

  Rich tooltip (tt-card style):
    <SectionHeader title="My Panel">
      {#snippet tooltip()}
        <div class="tt-title">Title</div>
        <div class="tt-desc">Description text</div>
        <div class="tt-row"><span class="tt-label">METRIC</span><span class="tt-meaning">Explanation</span></div>
      {/snippet}
    </SectionHeader>
-->
<script>
    import Tooltip from '$lib/components/ui/Tooltip.svelte';

    let {
        title = '',
        titleTooltip = '',
        subtitle = '',
        subtitleColor = '',
        subtitleFlag = '',
        size = 'md',
        border = false,
        action = null,
        tooltip = null,
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

    // Rich tooltip hover state
    let tipVisible = $state(false);
    let tipTimer;
    function showTip() { if (tipTimer) clearTimeout(tipTimer); tipVisible = true; }
    function hideTip() { tipTimer = setTimeout(() => { tipVisible = false; }, 150); }
    function keepTip() { if (tipTimer) clearTimeout(tipTimer); }
</script>

<div class="section-header {border ? 'section-header-border' : ''} {className}">
    <div class="section-header-text">
        {#if tooltip}
            <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_static_element_interactions -->
            <div class="relative inline-block" role="button"
                 onmouseenter={showTip} onmouseleave={hideTip}
                 onfocus={showTip} onblur={hideTip}
                 tabindex="0">
                <h3 class="{sizes.title} font-semibold uppercase tracking-[0.08em] {titleClass || 'text-muted'} cursor-default" aria-level={level}>
                    {title}
                </h3>
                {#if tipVisible}
                    <div class="tt-card" role="tooltip" onmouseenter={keepTip} onmouseleave={hideTip}>
                        {@render tooltip()}
                    </div>
                {/if}
            </div>
        {:else if titleTooltip}
            <Tooltip text={titleTooltip} position="bottom" wrap>
                <h3 class="{sizes.title} font-semibold uppercase tracking-[0.08em] {titleClass || 'text-muted'}" aria-level={level}>
                    {title}
                </h3>
            </Tooltip>
        {:else}
            <h3 class="{sizes.title} font-semibold uppercase tracking-[0.08em] {titleClass || 'text-muted'}" aria-level={level}>
                {title}
            </h3>
        {/if}
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

    .tt-card {
        position: absolute;
        top: calc(100% + 8px);
        left: 0;
        z-index: 9999;
        width: min(360px, calc(100vw - 2rem));
        padding: var(--space-md) var(--space-lg);
        background: var(--surface-overlay);
        border: 1px solid var(--border-default);
        border-radius: var(--radius-lg);
        backdrop-filter: blur(8px);
        box-shadow: var(--shadow-tooltip);
        pointer-events: auto;
    }

    /* Shared tooltip content classes */
    .tt-card :global(.tt-title) {
        font-size: 14px;
        font-weight: 600;
        color: var(--text-primary);
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 4px;
    }

    .tt-card :global(.tt-desc) {
        font-size: 13px;
        color: var(--text-muted);
        line-height: 1.5;
        margin-bottom: 10px;
    }

    .tt-card :global(.tt-row) {
        display: flex;
        align-items: start;
        gap: 8px;
        margin-bottom: 6px;
    }

    .tt-card :global(.tt-label) {
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        color: var(--text-secondary);
        width: 56px;
        flex-shrink: 0;
    }

    .tt-card :global(.tt-meaning) {
        font-size: 13px;
        color: var(--text-muted);
        line-height: 1.4;
    }
</style>
