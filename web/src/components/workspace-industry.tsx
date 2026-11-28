'use client';
import { useEffect, useState } from 'react';
import { listIndustryTemplates, type IndustryTemplate, type TemplateText } from '@/api/workspaces';
import { useCopy, useLocale } from '@/i18n/provider';

export function IndustryKnowledge({ items, heading: Heading }: { items: TemplateText[]; heading: 'h2' | 'h3' }) {
  const copy = useCopy('base');
  const locale = useLocale();
  return <section className="grid min-w-0 gap-2">
    <Heading className="font-semibold">{copy.industryKnowledge}</Heading>
    <ul className="list-disc space-y-1 pl-5 text-sm">{items.map(item => <li key={item.en}>{item[locale]}</li>)}</ul>
  </section>;
}

export function IndustryPicker({ value, onChange, disabled }: {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const copy = useCopy('base');
  const locale = useLocale();
  const [templates, setTemplates] = useState<IndustryTemplate[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const ctrl = new AbortController();
    listIndustryTemplates(ctrl.signal).then(rows => { if (!ctrl.signal.aborted) setTemplates(rows); })
      .catch(() => { if (!ctrl.signal.aborted) setFailed(true); });
    return () => ctrl.abort();
  }, []);
  const selected = templates?.find(row => row.id === value);
  return <div className="grid min-w-0 gap-3">
    <label className="grid min-w-0 gap-1">
      <span>{copy.industryTemplate}</span>
      <select value={value} onChange={event => onChange(event.target.value)} disabled={disabled}
        className="min-h-11 w-full min-w-0 rounded border border-border bg-surface px-3">
        <option value="">{copy.industryOther}</option>
        {templates?.map(row => <option key={row.id} value={row.id}>{row.name[locale]}</option>)}
      </select>
    </label>
    {!templates && !failed && <p role="status" className="text-sm text-muted">{copy.loading}</p>}
    {failed && <p role="alert" className="text-sm">{copy.industryUnavailable}</p>}
    {selected && <div className="grid min-w-0 gap-3 rounded-lg border border-border p-3">
      <p className="text-sm">{selected.description[locale]}</p>
      <IndustryKnowledge items={selected.suggested_knowledge} heading="h2" />
      <details className="text-sm">
        <summary className="min-h-11 cursor-pointer py-2 font-medium">{copy.industryExamples}</summary>
        <dl className="grid gap-2">{selected.sample_qa.map(example => <div key={example.question.en}>
          <dt className="font-medium">{example.question[locale]}</dt>
          <dd>{example.answer[locale]}</dd>
        </div>)}</dl>
      </details>
    </div>}
  </div>;
}
