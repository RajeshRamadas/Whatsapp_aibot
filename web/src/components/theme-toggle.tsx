'use client';
import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { Button } from './ui/button';
export function ThemeToggle({ label }: { label: string }) {
  const [dark, setDark] = useState(false);
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; }, [dark]);
  return <Button variant="secondary" aria-label={label} aria-pressed={dark} onClick={() => setDark(!dark)}>{dark ? <Sun aria-hidden /> : <Moon aria-hidden />}</Button>;
}
