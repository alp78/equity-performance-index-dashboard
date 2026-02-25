<!--
  Card — Surface container with elevation variants
  Variants: default, elevated, outlined, glass
-->
<script>
    let {
        variant = 'default',
        padding = true,
        hover = false,
        fill = false,
        role = undefined,
        'aria-label': ariaLabel = undefined,
        class: className = '',
        children,
    } = $props();

    const variantClasses = {
        default:  'bg-surface-1 border border-border-default',
        elevated: 'bg-surface-2 border border-border-default shadow-md',
        outlined: 'bg-transparent border border-border-strong',
        glass:    'bg-surface-1/80 backdrop-blur-sm border border-border-subtle',
    };
</script>

<div
    {role}
    aria-label={ariaLabel}
    class="rounded-[var(--radius-lg)] overflow-hidden transition-all
        {variantClasses[variant] || variantClasses.default}
        {padding ? 'p-[var(--space-xl)]' : ''}
        {fill ? 'h-full flex flex-col' : ''}
        {hover ? 'hover:-translate-y-0.5 hover:shadow-lg hover:border-border-strong cursor-pointer' : ''}
        {className}"
    style="transition-duration: var(--transition-default);"
>
    {@render children()}
</div>
