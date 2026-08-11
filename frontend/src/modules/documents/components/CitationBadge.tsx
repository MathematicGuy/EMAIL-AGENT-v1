import React, { useState } from 'react';
import type { EvidenceReference } from '../types';

interface CitationBadgeProps {
  index: number;
  evidence?: EvidenceReference;
  onClick?: () => void;
}

export const CitationBadge: React.FC<CitationBadgeProps> = ({ index, evidence, onClick }) => {
  const [isHovered, setIsHovered] = useState(false);

  const scorePct = evidence?.relevance_score != null
    ? Math.round(evidence.relevance_score * 100)
    : 95;

  const scoreBadgeColor =
    scorePct >= 85
      ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
      : scorePct >= 70
      ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
      : 'bg-slate-500/10 text-slate-400 border-slate-500/20';

  const locatorStr = evidence?.locator
    ? Object.entries(evidence.locator)
        .map(([k, v]) => `${k}: ${v}`)
        .join(', ')
    : 'Page 1';

  return (
    <span className="relative inline-block mx-0.5 align-baseline">
      <button
        type="button"
        onClick={onClick}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        className="inline-flex items-center justify-center min-w-[1.4rem] h-5 px-1.5 text-[11px] font-semibold tracking-tight rounded-md bg-indigo-500/15 text-indigo-400 border border-indigo-500/30 hover:bg-indigo-500/30 hover:border-indigo-400 transition-all cursor-pointer shadow-sm"
        aria-label={`Citation ${index}`}
      >
        [{index}]
      </button>

      {isHovered && evidence && (
        <div className="absolute left-1/2 bottom-full mb-2 -translate-x-1/2 w-72 p-3 rounded-xl border border-zinc-700 bg-zinc-900/95 backdrop-blur-md shadow-2xl text-xs z-50 animate-in fade-in slide-in-from-bottom-1 duration-150 pointer-events-none">
          <div className="flex items-center justify-between gap-2 pb-2 border-b border-zinc-800">
            <span className="font-medium text-zinc-200 truncate flex items-center gap-1.5">
              <span>📄</span>
              <span className="truncate">{evidence.source_id}</span>
            </span>
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono border ${scoreBadgeColor}`}>
              {scorePct}%
            </span>
          </div>

          <div className="mt-2 text-[11px] text-zinc-400">
            <span className="font-semibold text-zinc-300">{locatorStr}</span>
          </div>

          {evidence.excerpt && (
            <p className="mt-1.5 text-zinc-300 text-[11px] leading-relaxed italic line-clamp-3 bg-zinc-950/50 p-2 rounded-lg border border-zinc-800/60">
              "{evidence.excerpt}"
            </p>
          )}

          <div className="mt-2 text-[10px] text-indigo-400 text-right font-medium">
            Click to view in drawer →
          </div>
        </div>
      )}
    </span>
  );
};
