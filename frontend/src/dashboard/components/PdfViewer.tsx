import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  AlertCircle,
  FileText,
  Loader2,
  RefreshCw,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Minimize2
} from 'lucide-react';
import { API_BASE_URL } from '../../lib/apiConfig';

// Polyfill DOMMatrix for jsdom / Node test environments if absent
if (typeof globalThis !== 'undefined' && !('DOMMatrix' in globalThis)) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).DOMMatrix = class DOMMatrix {
    a = 1; b = 0; c = 0; d = 1; e = 0; f = 0;
    m11 = 1; m12 = 0; m13 = 0; m14 = 0;
    m21 = 0; m22 = 1; m23 = 0; m24 = 0;
    m31 = 0; m32 = 0; m33 = 1; m34 = 0;
    m41 = 0; m42 = 0; m43 = 0; m44 = 1;
    is2D = true;
    isIdentity = true;
  };
}

interface PdfViewerProps {
  filename: string;
  onFallbackToMarkdown?: () => void;
}

export const PdfViewer: React.FC<PdfViewerProps> = ({
  filename,
  onFallbackToMarkdown,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [rawBuffer, setRawBuffer] = useState<ArrayBuffer | null>(null);
  const [zoomLevel, setZoomLevel] = useState<number>(100);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const [pageCount, setPageCount] = useState<number>(0);

  // 1. Fetch PDF binary data
  const fetchDocument = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const url = `${API_BASE_URL}/api/v1/raw-documents/${encodeURIComponent(filename)}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`Không thể tải tệp PDF (Mã lỗi: ${res.status})`);
      }
      const buffer = await res.arrayBuffer();
      setRawBuffer(buffer);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Lỗi kết nối khi tải tài liệu');
    } finally {
      setIsLoading(false);
    }
  }, [filename]);

  useEffect(() => {
    void fetchDocument();
  }, [fetchDocument]);

  // 2. Render all PDF pages sequentially onto HTML Canvas
  useEffect(() => {
    let active = true;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let loadingTask: any = null;

    const renderPdf = async () => {
      if (!rawBuffer || !containerRef.current) return;

      containerRef.current.innerHTML = '';
      try {
        const [pdfjsLib, workerModule] = await Promise.all([
          import('pdfjs-dist'),
          import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
        ]);

        if (!active) return;
        pdfjsLib.GlobalWorkerOptions.workerSrc = workerModule.default;

        loadingTask = pdfjsLib.getDocument({
          data: new Uint8Array(rawBuffer),
          cMapUrl: 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/cmaps/',
          cMapPacked: true,
        });

        const pdfDoc = await loadingTask.promise;
        if (!active) return;

        setPageCount(pdfDoc.numPages);

        for (let pageNum = 1; pageNum <= pdfDoc.numPages; pageNum++) {
          if (!active || !containerRef.current) break;

          const page = await pdfDoc.getPage(pageNum);
          const viewport = page.getViewport({ scale: 1.5 });

          const canvas = document.createElement('canvas');
          canvas.className = 'pdf-page-canvas shadow-lg rounded-xs mb-4 bg-white block max-w-full';
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = '100%';
          canvas.style.height = 'auto';

          const context = canvas.getContext('2d');
          if (context) {
            await page.render({
              canvas,
              canvasContext: context,
              viewport,
            }).promise;
          }

          if (active && containerRef.current) {
            containerRef.current.appendChild(canvas);
          }
        }
      } catch (renderErr: unknown) {
        if (active) {
          console.error('pdfjs render error:', renderErr);
          setError('Không thể kết xuất trang tài liệu PDF này.');
        }
      }
    };

    void renderPdf();

    return () => {
      active = false;
      if (loadingTask) {
        void loadingTask.destroy();
      }
      if (containerRef.current) {
        containerRef.current.innerHTML = '';
      }
    };
  }, [rawBuffer]);

  // 3. Zoom Actions
  const handleZoomIn = () => setZoomLevel((z) => Math.min(200, z + 10));
  const handleZoomOut = () => setZoomLevel((z) => Math.max(50, z - 10));
  const handleZoomReset = () => setZoomLevel(100);
  const handleFitWidth = () => setZoomLevel(110);

  // 4. Toggle Fullscreen
  const toggleFullscreen = () => {
    if (!wrapperRef.current) return;
    if (!document.fullscreenElement) {
      wrapperRef.current.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  if (isLoading) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-[#141312] border border-[#2d2b27] rounded-xl text-zinc-400 p-8 space-y-3">
        <Loader2 className="w-7 h-7 animate-spin text-[#d97757]" />
        <p className="text-xs font-medium text-zinc-300">Đang nạp tài liệu PDF...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-[#181715] border border-[#33302b] rounded-2xl p-8 text-center max-w-xl mx-auto my-auto shadow-lg">
        <div className="w-12 h-12 rounded-full bg-amber-950/40 border border-amber-800/50 flex items-center justify-center mb-4 text-amber-400">
          <AlertCircle className="w-6 h-6" />
        </div>
        <h3 className="text-sm font-semibold text-white tracking-tight">
          Không thể hiển thị tài liệu PDF
        </h3>
        <p className="text-xs text-zinc-400 mt-2 leading-5">{error}</p>

        <div className="flex items-center gap-3 mt-6">
          <button
            onClick={() => void fetchDocument()}
            className="flex items-center gap-1.5 px-3.5 py-1.5 bg-[#2a2824] hover:bg-[#383530] text-zinc-200 rounded-lg text-xs font-semibold border border-[#3e3b36] transition-colors cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Thử lại</span>
          </button>

          {onFallbackToMarkdown && (
            <button
              onClick={onFallbackToMarkdown}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-[#d97757] hover:bg-[#e08862] text-[#1c1b18] rounded-lg text-xs font-semibold transition-colors cursor-pointer shadow-xs"
            >
              <FileText className="w-3.5 h-3.5" />
              <span>Xem văn bản trích xuất (Markdown)</span>
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      ref={wrapperRef}
      className={`w-full h-full flex flex-col rounded-xl overflow-hidden border border-[#2d2b27] bg-[#121110] shadow-2xl relative ${
        isFullscreen ? 'fixed inset-0 z-50 rounded-none border-0' : ''
      }`}
    >
      {/* ULTRA-MINIMAL TOOLBAR */}
      <div className="px-3 py-1.5 bg-[#1c1b18] border-b border-[#2d2b27] flex items-center justify-between gap-2 shrink-0 select-none z-10">
        <div className="text-[11px] text-zinc-400 font-medium pl-1">
          {pageCount > 0 && <span>{pageCount} trang</span>}
        </div>

        <div className="flex items-center gap-2">
          {/* Zoom Controls */}
          <div className="flex items-center gap-1 bg-[#252320] px-2 py-1 rounded-lg border border-[#33312d] text-zinc-400 text-xs">
            <button
              onClick={handleZoomOut}
              className="p-1 hover:text-white transition-colors cursor-pointer rounded hover:bg-[#33302b]"
              title="Thu nhỏ (-10%)"
            >
              <ZoomOut className="w-3.5 h-3.5" />
            </button>
            <span className="w-11 text-center font-mono text-[11px] text-zinc-200 font-semibold">
              {zoomLevel}%
            </span>
            <button
              onClick={handleZoomIn}
              className="p-1 hover:text-white transition-colors cursor-pointer rounded hover:bg-[#33302b]"
              title="Phóng to (+10%)"
            >
              <ZoomIn className="w-3.5 h-3.5" />
            </button>

            <div className="w-[1px] h-3 bg-zinc-700 mx-1" />

            <button
              onClick={handleZoomReset}
              className="px-1.5 py-0.5 text-[11px] hover:text-white transition-colors cursor-pointer rounded hover:bg-[#33302b]"
              title="Đặt lại 100%"
            >
              100%
            </button>
            <button
              onClick={handleFitWidth}
              className="px-1.5 py-0.5 text-[11px] hover:text-white transition-colors cursor-pointer rounded hover:bg-[#33302b]"
              title="Khớp chiều ngang"
            >
              Khớp
            </button>
          </div>

          {/* Fullscreen Button */}
          <button
            onClick={toggleFullscreen}
            className="p-1.5 bg-[#252320] hover:bg-[#33302b] text-zinc-300 rounded-lg border border-[#33312d] transition-colors cursor-pointer"
            title={isFullscreen ? 'Thu nhỏ cửa sổ' : 'Toàn màn hình'}
          >
            {isFullscreen ? (
              <Minimize2 className="w-3.5 h-3.5" />
            ) : (
              <Maximize2 className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* CONTINUOUS DOCUMENT PREVIEW CANVAS WRAPPER */}
      <div className="flex-1 w-full h-full overflow-auto p-4 sm:p-8 custom-scrollbar bg-[#1a1917] flex justify-center items-start">
        <div
          ref={containerRef}
          style={{
            transform: `scale(${zoomLevel / 100})`,
            transformOrigin: 'top center',
            transition: 'transform 0.15s ease-out',
          }}
          className="pdf-viewer-paper-root w-full max-w-4xl min-h-[842px] transition-all flex flex-col items-center"
        />
      </div>
    </div>
  );
};

export default PdfViewer;
