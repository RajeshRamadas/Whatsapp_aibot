import type { HTMLAttributes, InputHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('rounded-xl border border-border bg-surface p-5', className)} {...props} />;
}
export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className="w-full rounded-lg border border-border bg-surface px-3 text-foreground" {...props} />;
}
export function Badge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'success' | 'warning' | 'danger' | 'info' | 'neutral' }) {
  const colors = { success: 'text-green-700 dark:text-green-300', warning: 'text-amber-700 dark:text-amber-300', danger: 'text-red-700 dark:text-red-300', info: 'text-blue-700 dark:text-blue-300', neutral: 'text-muted' };
  return <span className={cn('inline-flex rounded-full border border-border px-3 py-1 text-sm', colors[tone])}>{children}</span>;
}
export function ChatBubble({ kind, children, label }: { kind: 'customer' | 'bot' | 'agent' | 'note'; children: React.ReactNode; label: string }) {
  return <div className={cn('max-w-full rounded-xl border border-border p-4 sm:max-w-[80%]', kind === 'customer' ? 'mr-auto bg-surface' : kind === 'note' ? 'w-full bg-amber-100 text-amber-950' : 'ml-auto bg-accent/10')}><strong className="block text-sm">{label}</strong><p className="mt-2 break-words">{children}</p></div>;
}
