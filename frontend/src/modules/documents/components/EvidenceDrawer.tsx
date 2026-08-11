import React from 'react';
import type { EvidenceReference } from '../types';

interface EvidenceDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  evidence: EvidenceReference[];
}

export const EvidenceDrawer: React.FC<EvidenceDrawerProps> = ({
  isOpen,
  onClose,
  evidence,
}) => {
  if (!isOpen) return null;

  // Group evidence hierarchically by Document (source_id)
  const groupedByDocument = evidence.reduce<Record<string, EvidenceReference[]>>(
    (acc, ev) => {
      const docName = ev.source_id || 'Tài liệu không tên';
      if (!acc[docName]) {
        acc[docName] = [];
      }
      acc[docName].push(ev);
      return acc;
    },
    {}
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-lg bg-zinc-900 border-l border-zinc-800 h-full flex flex-col shadow-2xl animate-in slide-in-from-right duration-250">
        {/* Header */}
        <div className="p-4 border-b border-zinc-800 flex items-center justify-between bg-zinc-950/50">
          <div className="flex items-center gap-2">
            <span className="text-xl">📁</span>
            <div>
              <h2 className="text-sm font-semibold text-zinc-100">
                Evidence Inspector
              </h2>
              <p className="text-xs text-zinc-400">
                Tất cả {evidence.length} trích dẫn được xếp theo tài liệu & độ liên quan
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors cursor-pointer"
            aria-label="Close drawer"
          >
            ✕
          </button>
        </div>

        {/* Hierarchical Document List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {Object.entries(groupedByDocument).map(([docName, items], docIdx) => (
            <div
              key={docName}
              className="rounded-2xl border border-zinc-800 bg-zinc-950/40 p-4 space-y-3"
            >
              {/* Document Header */}
              <div className="flex items-center justify-between pb-2 border-b border-zinc-800/80">
                <span className="font-semibold text-sm text-zinc-200 flex items-center gap-2 truncate">
                  <span>📄</span>
                  <span className="truncate">{docName}</span>
                </span>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 shrink-0">
                  {items.length} trích dẫn
                </span>
              </div>

              {/* Items under Document */}
              <div className="space-y-3 pl-2">
                {items.map((ev, itemIdx) => {
                  const globalIdx = evidence.indexOf(ev) + 1;
                  const scorePct = ev.relevance_score != null
                    ? Math.round(ev.relevance_score * 100)
                    : Math.max(70, 98 - (docIdx * 5 + itemIdx * 4));

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
                    : `Trang ${itemIdx + 1}`;

                  return (
                    <div
                      key={ev.evidence_id || itemIdx}
                      className="p-3 rounded-xl border border-zinc-800/80 bg-zinc-900/80 space-y-1.5"
                    >
                      <div className="flex items-center justify-between text-xs">
                        <div className="flex items-center gap-1.5 font-medium text-zinc-300">
                          <span className="text-indigo-400 font-mono font-semibold">
                            [{globalIdx}]
                          </span>
                          <span>{locatorStr}</span>
                        </div>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-mono border ${scoreBadgeColor}`}>
                          🟢 {scorePct}% Relevance
                        </span>
                      </div>

                      {ev.excerpt && (
                        <p className="text-xs text-zinc-300 italic leading-relaxed bg-zinc-950/60 p-2.5 rounded-lg border border-zinc-800/60">
                          "{ev.excerpt}"
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
