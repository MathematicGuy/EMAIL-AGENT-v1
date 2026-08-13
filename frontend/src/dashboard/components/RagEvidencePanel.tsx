import { useEffect, useRef, useState } from 'react';
import { FileText, X } from 'lucide-react';
import type { ChatRagEvidence, ChatRetrievalStatus } from '../types';

interface RagEvidencePanelProps {
  evidence: ChatRagEvidence[];
  retrievalStatus?: ChatRetrievalStatus;
}

function summaryText(evidence: ChatRagEvidence[], retrievalStatus?: ChatRetrievalStatus): string {
  if (evidence.length > 0) {
    return `RAG evidence · ${evidence.length} chunk${evidence.length === 1 ? '' : 's'} · ${retrievalStatus ?? 'success'}`;
  }
  return `RAG evidence · ${retrievalStatus === 'no_results' ? 'no results' : retrievalStatus ?? 'unavailable'}`;
}

function emptyMessage(retrievalStatus?: ChatRetrievalStatus): string {
  if (retrievalStatus === 'no_results') return 'No matching chunks were retrieved.';
  if (retrievalStatus === 'timeout') return 'Retrieval timed out before any chunks were returned.';
  return 'Retrieval was unavailable for this response.';
}

export function RagEvidencePanel({ evidence, retrievalStatus }: RagEvidencePanelProps) {
  const [selectedEvidence, setSelectedEvidence] = useState<ChatRagEvidence | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (selectedEvidence) closeButtonRef.current?.focus();
  }, [selectedEvidence]);

  return (
    <>
      <details
        open={isOpen}
        className="mt-3 rounded-lg border border-[#38342f] bg-[#211f1c] text-xs"
      >
        <summary
          onClick={(event) => {
            event.preventDefault();
            setIsOpen((current) => !current);
          }}
          className="cursor-pointer px-3 py-2 text-zinc-400 hover:text-zinc-200"
        >
          {summaryText(evidence, retrievalStatus)}
        </summary>
        {isOpen && <div className="space-y-2 border-t border-[#38342f] p-2.5">
          {evidence.length === 0 ? (
            <p role="status" className="text-zinc-400">{emptyMessage(retrievalStatus)}</p>
          ) : evidence.map((item, index) => (
            <article key={item.chunkId} className="rounded-md border border-[#48433d] bg-[#292722] p-2.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-zinc-200">
                <FileText className="h-3.5 w-3.5 text-[#d97757]" aria-hidden="true" />
                <span className="font-medium">{index + 1}. {item.documentTitle}</span>
                {item.section && <span className="text-zinc-500">{item.section}</span>}
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-zinc-400">
                <span>Relevance {item.relevanceScore.toFixed(3)}</span>
                {item.rerankScore !== null && <span>Rerank {item.rerankScore.toFixed(3)}</span>}
              </div>
              <p className="mt-2 whitespace-pre-wrap text-zinc-300">{item.preview}</p>
              <button
                type="button"
                onClick={() => setSelectedEvidence(item)}
                className="mt-2 rounded-md border border-[#5a5149] px-2 py-1 text-zinc-200 hover:bg-[#38342f]"
              >
                View full chunk
              </button>
            </article>
          ))}
        </div>}
      </details>

      {selectedEvidence && (
        <dialog
          open
          aria-label={`Retrieved chunk: ${selectedEvidence.documentTitle}`}
          onCancel={(event) => { event.preventDefault(); setSelectedEvidence(null); }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setSelectedEvidence(null);
          }}
          className="fixed inset-0 m-auto max-h-[80vh] w-[min(42rem,calc(100vw-2rem))] rounded-xl border border-[#48433d] bg-[#211f1c] p-0 text-zinc-200 shadow-2xl backdrop:bg-black/60"
        >
          <div className="flex items-center justify-between border-b border-[#38342f] px-4 py-3">
            <div>
              <h2 className="text-sm font-medium">{selectedEvidence.documentTitle}</h2>
              {selectedEvidence.section && <p className="mt-0.5 text-xs text-zinc-500">{selectedEvidence.section}</p>}
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={() => setSelectedEvidence(null)}
              aria-label="Close full chunk"
              className="rounded-md p-1 text-zinc-400 hover:bg-[#38342f] hover:text-zinc-100"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
          <p className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap px-4 py-3 text-sm leading-6">{selectedEvidence.content}</p>
        </dialog>
      )}
    </>
  );
}
