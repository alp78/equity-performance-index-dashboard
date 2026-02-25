<!--
  NavItem — Sidebar navigation button with icon + label + accent bar
-->
<script>
    let { icon = null, label = '', active = false, onclick } = $props();
</script>

<button
    class="nav-item"
    class:nav-active={active}
    {onclick}
>
    <div class="nav-accent" class:visible={active}></div>
    {#if icon}
        {@const Icon = icon}
        <Icon size={20} strokeWidth={active ? 2.5 : 2} />
    {/if}
    <span class="nav-label">{label}</span>
</button>

<style>
    .nav-item {
        display: flex;
        align-items: center;
        gap: var(--space-md);
        width: 100%;
        height: 52px;
        padding: 0 var(--space-lg);
        border: none;
        background: transparent;
        color: var(--text-muted);
        font-size: 17px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        cursor: pointer;
        position: relative;
        transition: all var(--transition-default);
    }
    .nav-item:hover {
        background: var(--hover-overlay);
        color: var(--text-secondary);
    }
    .nav-active {
        background: var(--selected-overlay);
        color: var(--text-primary);
    }
    .nav-accent {
        position: absolute;
        left: 0;
        top: 4px;
        bottom: 4px;
        width: 3px;
        border-radius: 0 2px 2px 0;
        background: var(--accent-primary);
        opacity: 0;
        transition: opacity var(--transition-default);
    }
    .nav-accent.visible {
        opacity: 1;
    }
    .nav-active :global(svg) {
        color: var(--accent-primary);
    }
    .nav-label {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
</style>
