import React from 'react';
import type { EvidenceReference } from '../types';

function formatLocator(locator: Record<string, unknown>): string {
  return Object.entries(locator)
    .map(([key, value]) => `${key}: ${value}`)
    .join(', ');
}

export const EvidenceList: React.FC<{ evidence: EvidenceReference[] }> = ({ evidence }) => {
  if (evidence.length === 0) {
    return <p className="text-xs text-[#6c6862]">Không có evidence nào cho mục này.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {evidence.map((ev) => (
        <li key={ev.evidence_id} className="rounded-lg border border-[#33312e] bg-[#22211e] px-3 py-2 text-xs">
          <div className="flex flex-wrap items-center gap-x-2 text-[#949089]">
            <span className="font-mono text-[#d97757]">{ev.source_id}</span>
            <span>v{ev.source_version}</span>
            <span>·</span>
            <span>{formatLocator(ev.locator)}</span>
          </div>
          {ev.excerpt && <p className="mt-1 text-[#f3f2ef]">"{ev.excerpt}"</p>}
        </li>
      ))}
    </ul>
  );
};
