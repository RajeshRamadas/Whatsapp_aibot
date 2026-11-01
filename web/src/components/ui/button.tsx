import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';
const styles = cva('inline-flex min-h-11 items-center justify-center rounded-lg px-4 font-medium transition-colors focus-visible:outline-2 disabled:opacity-50', { variants: { variant: { primary: 'bg-accent text-accent-foreground', secondary: 'border border-border bg-surface text-foreground', ghost: 'text-foreground hover:bg-border', destructive: 'bg-red-700 text-white' } }, defaultVariants: { variant: 'primary' } });
type Props = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof styles> & { asChild?: boolean };
export function Button({ variant, className, asChild = false, ...props }: Props) {
  const Component = asChild ? Slot : 'button';
  return <Component className={cn(styles({ variant }), className)} {...props} />;
}
