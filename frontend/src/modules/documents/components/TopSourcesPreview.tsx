import React from 'react';
import type { EvidenceReference } from '../types';

interface TopSourcesPreviewProps {
  evidence: EvidenceReference[];
  onOpenDrawer: () => void;
}

export const TopSourcesPreview: React.FC<TopSourcesPreviewProps> = ({
  evidence,
  onOpenDrawer,
}) => {
  if (!evidence || evidence.length === 0) return null;

  // Top 3 primary sources
  const top3 = evidence.slice(0, 3);
  const remainingCount = Math.max(0, evidence.length - 3);

  return (
    <div className="mt-4 pt-3 border-t border-zinc-800/60">
      <div className="flex items-center justify-between mb-2.5">
        <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
          <span>📁</span> Nguồn trích dẫn hàng đầu ({evidence.length})
        </span>
        {remainingCount > 0 && (
          <button
            type="button"
            onClick={onOpenDrawer}
            className="text-xs font-medium text-indigo-400 hover:text-indigo-300 transition-colors flex items-center gap-1 cursor-pointer"
          >
            <span>+ {remainingCount} nguồn trích dẫn khác</span>
            <span>→</span>
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {top3.map((ev, idx) => {
          const scorePct = ev.relevance_score != null
            ? Math.round(ev.relevance_score * 100)
            : 95 - idx * 4;

          const scoreBadgeColor =
            scorePct >= 85
              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
              : scorePct >= 70
              ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
              : 'bg-slate-500/10 text-slate-400 border-slate-500/20';

          const locatorStr = ev.locator
            ? Object.entries(ev.locator)
                .map(([k, v]) => `${k}: ${v}`)
                .join(', ')
            : `Trang ${idx + 1}`;

          return (
            <div
              key={ev.evidence_id || idx}
              onClick={onOpenDrawer}
              className="group p-2.5 rounded-xl border border-zinc-800 bg-zinc-900/60 hover:bg-zinc-800/60 hover:border-zinc-700 transition-all cursor-pointer shadow-sm flex flex-col justify-between"
            >
              <div>
                <div className="flex items-center justify-between gap-1 mb-1.5">
                  <span className="text-xs font-medium text-zinc-200 truncate flex items-center gap-1">
                    <span className="text-indigo-400 font-mono text-[11px]">[{idx + 1}]</span>
                    <span className="truncate">{ev.source_id}</span>
                  </span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono border ${scoreBadgeColor}`}>
                    {scorePct}%
                  </span>
                </div>
                <div className="text-[10px] text-zinc-400 mb-1 font-medium">{locatorStr}</div>
                {ev.excerpt && (
                  <p className="text-[11px] text-zinc-400 line-clamp-2 italic leading-snug">
                    "{ev.excerpt}"
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
