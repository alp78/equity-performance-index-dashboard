<!--
  Tooltip — Design-system-styled tooltip with configurable position
  Portals to document.body to escape container-type containment contexts.
  Usage:
    <Tooltip text="Explanation here" position="top">
      <button>Hover me</button>
    </Tooltip>
-->
<script>
    import { browser } from '$app/environment';
    import { onDestroy } from 'svelte';

    let {
        text = '',
        position = 'top',
        delay = 200,
        wrap = false,
        class: className = '',
        children,
    } = $props();

    let timer;
    let wrapperEl;
    let portalEl = null;

    function calcStyle() {
        if (!wrapperEl) return '';
        const r = wrapperEl.getBoundingClientRect();
        const g = 6;
        let s = '';
        switch (position) {
            case 'bottom':
                s = `top:${r.bottom + g}px;left:${r.left + r.width / 2}px;transform:translateX(-50%)`;
                break;
            case 'top':
                s = `bottom:${window.innerHeight - r.top + g}px;left:${r.left + r.width / 2}px;transform:translateX(-50%)`;
                break;
            case 'left':
                s = `top:${r.top + r.height / 2}px;right:${window.innerWidth - r.left + g}px;transform:translateY(-50%)`;
                break;
            case 'right':
                s = `top:${r.top + r.height / 2}px;left:${r.right + g}px;transform:translateY(-50%)`;
                break;
        }
        return s;
    }

    function show() {
        if (!text || !browser) return;
        timer = setTimeout(() => {
            removePortal();
            const el = document.createElement('div');
            el.className = 'tt-portal' + (wrap ? ' tt-portal-wrap' : '');
            el.setAttribute('role', 'tooltip');
            el.textContent = text;
            el.style.cssText = calcStyle();
            document.body.appendChild(el);
            portalEl = el;
        }, delay);
    }

    function hide() {
        clearTimeout(timer);
        removePortal();
    }

    function removePortal() {
        if (portalEl) { portalEl.remove(); portalEl = null; }
    }

    onDestroy(() => { clearTimeout(timer); removePortal(); });
</script>

<div
    class="tooltip-wrapper {className}"
    role="presentation"
    bind:this={wrapperEl}
    onmouseenter={show}
    onmouseleave={hide}
    onfocusin={show}
    onfocusout={hide}
>
    {@render children()}
</div>

<style>
    .tooltip-wrapper {
        position: relative;
        display: inline-flex;
    }

    :global(.tt-portal) {
        position: fixed;
        z-index: 9999;
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
    }

    :global(.tt-portal-wrap) {
        white-space: normal;
        max-width: min(340px, calc(100vw - 2rem));
        text-wrap: pretty;
    }
</style>
