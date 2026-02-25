<!--
  Badge — Status indicators and tags
  Variants: positive, negative, neutral, warning, info, accent
-->
<script>
    let {
        variant = 'neutral',
        size = 'sm',
        class: className = '',
        children,
    } = $props();

    const variantStyles = {
        positive: 'bg-positive-bg text-positive',
        negative: 'bg-negative-bg text-negative',
        neutral:  'bg-surface-2 text-secondary',
        warning:  'bg-warning-bg text-warning',
        info:     'bg-info-bg text-info',
        accent:   'bg-selected-overlay text-accent',
    };

    const sizeStyles = {
        xs: 'text-[10px] px-1.5 py-0.5',
        sm: 'text-[11px] px-2 py-0.5',
        md: 'text-[12px] px-2.5 py-1',
    };
</script>

<span
    class="inline-flex items-center font-semibold uppercase tracking-wider rounded-[var(--radius-sm)] whitespace-nowrap
        {variantStyles[variant] || variantStyles.neutral}
        {sizeStyles[size] || sizeStyles.sm}
        {className}"
>
    {@render children()}
</span>
