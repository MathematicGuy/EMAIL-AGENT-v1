import React, { useEffect, useRef, useState, useCallback } from 'react';
import { renderAsync } from 'docx-preview';
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

interface DocxViewerProps {
  filename: string;
  onFallbackToMarkdown?: () => void;
}

export const DocxViewer: React.FC<DocxViewerProps> = ({
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

  // 1. Fetch DOCX binary data
  const fetchDocument = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const url = `${API_BASE_URL}/api/v1/raw-documents/${encodeURIComponent(filename)}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`Không thể tải tệp DOCX (Mã lỗi: ${res.status})`);
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

  // 2. Continuous seamless rendering with docx-preview
  useEffect(() => {
    let active = true;
    if (rawBuffer && containerRef.current) {
      containerRef.current.innerHTML = '';
      void renderAsync(rawBuffer, containerRef.current, undefined, {
        className: 'docx-preview-paper',
        inWrapper: true,
        ignoreWidth: false,
        ignoreHeight: false,
        breakPages: false, // Luôn để liền mạch
        renderHeaders: true,
        renderFooters: true,
        renderFootnotes: true,
        renderEndnotes: true,
        experimental: true,
        useBase64URL: true,
      }).catch((renderErr) => {
        if (active) {
          console.error('docx-preview render error:', renderErr);
          setError('Không thể kết xuất trang tài liệu DOCX này.');
        }
      });
    }

    return () => {
      active = false;
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
        <p className="text-xs font-medium text-zinc-300">Đang nạp văn bản Word...</p>
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
          Không thể hiển thị tài liệu DOCX
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
      {/* ULTRA-MINIMAL TOOLBAR (ZOOM & FULLSCREEN ONLY) */}
      <div className="px-3 py-1.5 bg-[#1c1b18] border-b border-[#2d2b27] flex items-center justify-end gap-2 shrink-0 select-none z-10">
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

      {/* CONTINUOUS DOCUMENT PREVIEW CANVAS WRAPPER */}
      <div className="flex-1 w-full h-full overflow-auto p-4 sm:p-8 custom-scrollbar bg-[#1a1917] flex justify-center items-start">
        <div
          ref={containerRef}
          style={{
            transform: `scale(${zoomLevel / 100})`,
            transformOrigin: 'top center',
            transition: 'transform 0.15s ease-out',
          }}
          className="docx-viewer-paper-root w-full max-w-4xl min-h-[842px] transition-all flex flex-col items-center"
        />
      </div>
    </div>
  );
};

export default DocxViewer;
