import { X, BrainCircuit, Files } from 'lucide-react';
import type { ChatExecutionTrace } from '../types';

interface ExecutionTraceDrawerProps {
  trace?: ChatExecutionTrace;
  onClose: () => void;
}

export function ExecutionTraceDrawer({ trace, onClose }: ExecutionTraceDrawerProps) {
  return (
    <aside
      aria-label="Chi tiết xử lý của mô hình"
      className="flex min-h-0 flex-col border-l border-[#413b34] bg-[#201e1b]"
    >
      <header className="flex items-start justify-between gap-4 border-b border-[#413b34] px-5 py-4">
        <div>
          <p className="text-sm font-semibold text-zinc-100">Chi tiết xử lý</p>
          {trace ? (
            <p className="mt-1 text-xs text-zinc-400">{trace.provider} · {trace.model} · {trace.mode === 'fast' ? 'Nhanh' : 'Suy luận'}</p>
          ) : (
            <p className="mt-1 text-xs text-zinc-400">Đang chờ mô hình trả chi tiết.</p>
          )}
        </div>
        <button type="button" onClick={onClose} className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label="Đóng chi tiết xử lý">
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 space-y-5 overflow-y-auto p-5">
        <section>
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-200"><BrainCircuit className="h-4 w-4 text-[#e8a78f]" /> Lập luận mô hình</h2>
          {trace?.reasoning ? (
            <pre className="mt-3 whitespace-pre-wrap break-words rounded-lg border border-[#413b34] bg-[#181715] p-3 font-sans text-xs leading-5 text-zinc-300">{trace.reasoning}</pre>
          ) : (
            <p className="mt-2 text-sm text-zinc-500">Nhà cung cấp không trả reasoning cho lượt này.</p>
          )}
          {trace?.reasoningTruncated && <p className="mt-2 text-xs text-amber-300">Reasoning đã được rút gọn để lưu an toàn.</p>}
        </section>

        <section>
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-200"><Files className="h-4 w-4 text-[#e8a78f]" /> Tài liệu đã truy xuất</h2>
          {trace?.retrievedFilenames.length ? (
            <ul className="mt-3 space-y-2">
              {trace.retrievedFilenames.map((filename) => <li key={filename} className="rounded-md border border-[#413b34] bg-[#181715] px-3 py-2 text-sm text-zinc-300">{filename}</li>)}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-zinc-500">Không có tệp nào được truy xuất.</p>
          )}
        </section>
      </div>
    </aside>
  );
}
