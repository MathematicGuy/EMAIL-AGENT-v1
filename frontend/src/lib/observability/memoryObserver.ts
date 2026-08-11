/**
 * Memory Observability & RAM Metrics Utility
 * Leverages Performance API & Chromium performance.memory (where available)
 */

export interface MemorySnapshot {
  timestamp: number;
  usedHeapMB: number;
  totalHeapMB: number;
  heapLimitMB: number;
  usedHeapBytes: number;
  totalHeapBytes: number;
  heapLimitBytes: number;
}

export interface StartupPerformanceReport {
  navigationType: string;
  domContentLoadedMs: number;
  loadEventMs: number;
  startupRAM: MemorySnapshot | null;
}

declare global {
  interface Performance {
    memory?: {
      usedJSHeapSize: number;
      totalJSHeapSize: number;
      jsHeapSizeLimit: number;
    };
  }
  interface Window {
    __MEMORY_METRICS__?: {
      startup: MemorySnapshot | null;
      history: MemorySnapshot[];
      getReport: () => StartupPerformanceReport;
    };
  }
}

/**
 * Capture current JS Heap RAM usage snapshot
 */
export function captureMemorySnapshot(): MemorySnapshot | null {
  if (typeof window === 'undefined' || !window.performance || !window.performance.memory) {
    return null;
  }

  const { usedJSHeapSize, totalJSHeapSize, jsHeapSizeLimit } = window.performance.memory;
  const toMB = (bytes: number) => Number((bytes / (1024 * 1024)).toFixed(2));

  return {
    timestamp: Date.now(),
    usedHeapMB: toMB(usedJSHeapSize),
    totalHeapMB: toMB(totalJSHeapSize),
    heapLimitMB: toMB(jsHeapSizeLimit),
    usedHeapBytes: usedJSHeapSize,
    totalHeapBytes: totalJSHeapSize,
    heapLimitBytes: jsHeapSizeLimit,
  };
}

/**
 * Initialize Startup Observability Layer
 */
export function initStartupObservability(): StartupPerformanceReport {
  const startupSnapshot = captureMemorySnapshot();
  const history: MemorySnapshot[] = startupSnapshot ? [startupSnapshot] : [];

  const navEntry = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;

  const report: StartupPerformanceReport = {
    navigationType: navEntry?.type || 'navigate',
    domContentLoadedMs: navEntry ? Math.round(navEntry.domContentLoadedEventEnd - navEntry.startTime) : 0,
    loadEventMs: navEntry ? Math.round(navEntry.loadEventEnd - navEntry.startTime) : 0,
    startupRAM: startupSnapshot,
  };

  window.__MEMORY_METRICS__ = {
    startup: startupSnapshot,
    history,
    getReport: () => ({
      ...report,
      startupRAM: captureMemorySnapshot() || startupSnapshot,
    }),
  };

  if (import.meta.env?.DEV) {
    console.log('[Observability] 🚀 Startup RAM Metrics:', {
      'Used Heap (RAM)': startupSnapshot ? `${startupSnapshot.usedHeapMB} MB` : 'N/A',
      'Total Heap Allocated': startupSnapshot ? `${startupSnapshot.totalHeapMB} MB` : 'N/A',
      'DOMContentLoaded': `${report.domContentLoadedMs} ms`,
    });
  }

  return report;
}
