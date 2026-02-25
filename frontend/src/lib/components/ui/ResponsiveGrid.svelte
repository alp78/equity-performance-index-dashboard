<!--
  ResponsiveGrid — auto-fit grid wrapper
  Usage:
    <ResponsiveGrid minWidth="280px" gap="xl">
      <Card>...</Card>
      <Card>...</Card>
    </ResponsiveGrid>
-->
<script>
    let {
        minWidth = '280px',
        gap = 'xl',
        class: className = '',
        children,
    } = $props();

    const gapMap = {
        xs: 'var(--space-xs)',
        sm: 'var(--space-sm)',
        md: 'var(--space-md)',
        lg: 'var(--space-lg)',
        xl: 'var(--space-xl)',
        '2xl': 'var(--space-2xl)',
    };

    let gridGap = $derived(gapMap[gap] || gapMap.xl);
</script>

<div
    class="responsive-grid {className}"
    style="display:grid;grid-template-columns:repeat(auto-fit,minmax({minWidth},1fr));gap:{gridGap};"
>
    {@render children()}
</div>
