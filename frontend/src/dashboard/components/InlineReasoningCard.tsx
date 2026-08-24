import React, { useEffect, useId, useRef, useState } from 'react';
import { Check, ChevronDown, Copy } from 'lucide-react';
import type { ChatExecutionTrace, ChatGenerationStatus } from '../types';
import { formatSecondsVi } from './reasoningDuration';

export interface InlineReasoningCardProps {
  executionTrace?: ChatExecutionTrace;
  generationStatus?: ChatGenerationStatus;
}

export const InlineReasoningCard: React.FC<InlineReasoningCardProps> = ({
  executionTrace,
  generationStatus,
}) => {
  const isGenerating = generationStatus === 'generating';
  const startedRef = useRef<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number>(0);
  const [finalMs, setFinalMs] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<boolean>(isGenerating);
  const [copied, setCopied] = useState<boolean>(false);
  const panelId = useId();

  // Live stopwatch while the model is thinking; freezes and collapses once the turn settles.
  useEffect(() => {
    if (!isGenerating) {
      if (startedRef.current !== null) {
        setFinalMs(Date.now() - startedRef.current);
        startedRef.current = null;
        setExpanded(false);
      }
      return;
    }
    if (startedRef.current === null) startedRef.current = Date.now();
    const started = startedRef.current;
    setExpanded(true);
    setElapsedMs(Date.now() - started);
    const timer = window.setInterval(() => setElapsedMs(Date.now() - started), 100);
    return () => window.clearInterval(timer);
  }, [isGenerating]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const reasoning = executionTrace?.reasoning;
  const isFastMode = executionTrace?.mode === 'fast';
  if (!isGenerating && !reasoning && !isFastMode) return null;

  const duration = isGenerating ? formatSecondsVi(elapsedMs) : finalMs === null ? null : formatSecondsVi(finalMs);
  const model = executionTrace?.model;
  const summary = isGenerating
    ? `Đang suy luận... (${duration}s)`
    : [duration ? `Đã suy luận trong ${duration} giây` : 'Chuỗi suy luận', model].filter(Boolean).join(' · ');

  const handleCopy = () => {
    if (!reasoning) return;
    void navigator.clipboard?.writeText(reasoning);
    setCopied(true);
  };

  return (
    <section
      className="mb-3 overflow-hidden rounded-xl border border-[#413b34] bg-[#24211d]"
      aria-label="Suy luận của mô hình"
    >
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={() => setExpanded((prev) => !prev)}
        className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-xs transition-colors hover:bg-[#2c2823]"
      >
        <span className="flex items-center gap-2 font-medium text-[#e8a78f]">
          <span aria-hidden="true">{isGenerating ? '🧠' : '✦'}</span>
          <span className={isGenerating ? 'animate-pulse motion-reduce:animate-none' : ''}>{summary}</span>
        </span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-zinc-400 transition-transform motion-reduce:transition-none ${expanded ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>

      {expanded && (
        <div id={panelId} className="space-y-2 border-t border-[#3a352f] px-4 py-3">
          {reasoning ? (
            <>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold text-zinc-300">Chuỗi suy luận (Chain of Thought)</span>
                <div className="flex items-center gap-2">
                  {executionTrace?.reasoningTruncated && (
                    <span className="text-[10px] text-amber-300">Đã rút gọn</span>
                  )}
                  <button
                    type="button"
                    onClick={handleCopy}
                    aria-label="Sao chép chuỗi suy luận"
                    className="inline-flex items-center gap-1 rounded border border-[#413b34] bg-[#1b1a17] px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:text-zinc-100"
                  >
                    {copied ? (
                      <>
                        <Check className="h-3 w-3 text-emerald-400" aria-hidden="true" /> Đã sao chép!
                      </>
                    ) : (
                      <>
                        <Copy className="h-3 w-3" aria-hidden="true" /> Sao chép
                      </>
                    )}
                  </button>
                </div>
              </div>
              <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-[#413b34] bg-[#161513] p-3 font-mono text-xs leading-5 text-zinc-300">
                {reasoning}
              </pre>
            </>
          ) : isFastMode ? (
            <p className="text-xs text-zinc-400">
              <span className="font-semibold text-zinc-300">Chế độ Nhanh:</span> Mô hình trả lời trực tiếp, bước suy
              luận sâu đã được tắt để giảm độ trễ.
            </p>
          ) : (
            <p className="text-xs text-[#e8a78f] animate-pulse motion-reduce:animate-none">
              Đang chờ mô hình trả chuỗi suy luận...
            </p>
          )}
        </div>
      )}
    </section>
  );
};
