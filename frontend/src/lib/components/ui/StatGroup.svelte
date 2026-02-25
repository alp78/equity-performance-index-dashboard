<!--
  StatGroup — Responsive row/grid of Stat components
  Wraps children in a flex/grid layout that adapts to available space.
-->
<script>
    let {
        columns = 'auto',
        gap = 'md',
        class: className = '',
        children,
    } = $props();

    const gapMap = {
        sm: 'var(--space-sm)',
        md: 'var(--space-lg)',
        lg: 'var(--space-xl)',
    };

    let gridStyle = $derived(
        columns === 'auto'
            ? `display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: ${gapMap[gap] || gapMap.md};`
            : `display:grid; grid-template-columns: repeat(${columns}, 1fr); gap: ${gapMap[gap] || gapMap.md};`
    );
</script>

<div class={className} style={gridStyle}>
    {@render children()}
</div>
