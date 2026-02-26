<!--
  Tooltip — Design-system-styled tooltip with configurable position
  Usage:
    <Tooltip text="Explanation here" position="top">
      <button>Hover me</button>
    </Tooltip>
-->
<script>
    let {
        text = '',
        position = 'top',
        delay = 200,
        wrap = false,
        class: className = '',
        children,
    } = $props();

    let visible = $state(false);
    let timer;

    function show() {
        timer = setTimeout(() => { visible = true; }, delay);
    }

    function hide() {
        clearTimeout(timer);
        visible = false;
    }
</script>

<div
    class="tooltip-wrapper {className}"
    role="presentation"
    onmouseenter={show}
    onmouseleave={hide}
    onfocusin={show}
    onfocusout={hide}
>
    {@render children()}
    {#if visible && text}
        <div class="tooltip-content tooltip-{position}" class:tooltip-wrap={wrap} role="tooltip">
            {text}
        </div>
    {/if}
</div>

<style>
    .tooltip-wrapper {
        position: relative;
        display: inline-flex;
    }

    .tooltip-content {
        position: absolute;
        z-index: var(--z-tooltip, 50);
        padding: var(--space-xs) var(--space-sm);
        background: var(--surface-overlay);
        color: var(--text-primary);
        font-size: var(--text-xs);
        font-family: var(--font-sans);
        line-height: 1.4;
        border: 1px solid var(--border-default);
        border-radius: var(--radius-sm);
        box-shadow: var(--shadow-md);
        backdrop-filter: blur(8px);
        white-space: nowrap;
        pointer-events: none;
        animation: fadeInUp 0.15s ease-out;
    }

    .tooltip-top {
        bottom: calc(100% + 6px);
        left: 50%;
        transform: translateX(-50%);
    }

    .tooltip-bottom {
        top: calc(100% + 6px);
        left: 50%;
        transform: translateX(-50%);
    }

    .tooltip-left {
        right: calc(100% + 6px);
        top: 50%;
        transform: translateY(-50%);
    }

    .tooltip-right {
        left: calc(100% + 6px);
        top: 50%;
        transform: translateY(-50%);
    }

    .tooltip-wrap {
        white-space: normal;
        max-width: 260px;
        text-wrap: pretty;
    }
</style>
