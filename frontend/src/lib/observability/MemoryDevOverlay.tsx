import { useEffect, useState } from 'react';
import { captureMemorySnapshot, type MemorySnapshot } from './memoryObserver';

export function MemoryDevOverlay() {
  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(() => captureMemorySnapshot());
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const interval = setInterval(() => {
      const current = captureMemorySnapshot();
      if (current) {
        setSnapshot(current);
        if (window.__MEMORY_METRICS__) {
          window.__MEMORY_METRICS__.history.push(current);
          if (window.__MEMORY_METRICS__.history.length > 100) {
            window.__MEMORY_METRICS__.history.shift();
          }
        }
      }
    }, 1500);

    return () => clearInterval(interval);
  }, []);

  if (!snapshot) {
    return null;
  }

  const startupRAM = window.__MEMORY_METRICS__?.startup?.usedHeapMB ?? snapshot.usedHeapMB;
  const diffMB = Number((snapshot.usedHeapMB - startupRAM).toFixed(2));
  const diffColor = diffMB > 5 ? 'text-red-400' : diffMB < 0 ? 'text-emerald-400' : 'text-zinc-400';

  return (
    <div className="fixed bottom-3 right-3 z-50 font-mono text-xs shadow-2xl rounded-lg border border-zinc-800 bg-zinc-950/90 text-zinc-200 backdrop-blur-md transition-all select-none">
      <div
        className="flex items-center gap-3 px-3 py-2 cursor-pointer"
        onClick={() => setExpanded((prev) => !prev)}
      >
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          </span>
          <span className="font-semibold text-zinc-100">RAM:</span>
          <span className="text-emerald-400 font-bold">{snapshot.usedHeapMB} MB</span>
        </div>
        <span className="text-zinc-500">|</span>
        <div className="text-zinc-400">
          Startup: <span className="text-zinc-200">{startupRAM} MB</span>
        </div>
        <span className={`text-[10px] ${diffColor}`}>
          ({diffMB >= 0 ? `+${diffMB}` : diffMB} MB)
        </span>
        <span className="text-zinc-500 text-[10px] ml-1">{expanded ? '▼' : '▲'}</span>
      </div>

      {expanded && (
        <div className="border-t border-zinc-800/80 px-3 py-2.5 space-y-1.5 text-[11px] bg-zinc-900/50">
          <div className="flex justify-between gap-4">
            <span className="text-zinc-400">Heap Allocated:</span>
            <span className="text-zinc-200">{snapshot.totalHeapMB} MB</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-zinc-400">V8 Heap Limit:</span>
            <span className="text-zinc-200">{snapshot.heapLimitMB} MB</span>
          </div>
          <div className="flex justify-between gap-4 border-t border-zinc-800 pt-1 mt-1">
            <span className="text-zinc-400">DOM ContentLoaded:</span>
            <span className="text-indigo-400">
              {window.__MEMORY_METRICS__?.getReport().domContentLoadedMs || 0} ms
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
