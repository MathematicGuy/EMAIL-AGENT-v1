import React, { useState, useEffect, useCallback } from 'react';
import { DocumentEditor, type DocumentEditorProps } from '@onlyoffice/document-editor-react';
import { AlertCircle, RefreshCw, FileText, Loader2 } from 'lucide-react';
import { API_BASE_URL } from '../../lib/apiConfig';

interface OnlyOfficeDocConfig {
  document: {
    fileType: string;
    key: string;
    title: string;
    url: string;
  };
  documentType: string;
  editorConfig: {
    callbackUrl: string;
    lang?: string;
    user?: {
      id: string;
      name: string;
    };
    customization?: {
      forcesave?: boolean;
      autosave?: boolean;
    };
  };
  documentServerUrl: string;
}

interface OnlyOfficeDocEditorProps {
  filename: string;
  onFallbackToMarkdown?: () => void;
}

export const OnlyOfficeDocEditor: React.FC<OnlyOfficeDocEditorProps> = ({
  filename,
  onFallbackToMarkdown,
}) => {
  const [config, setConfig] = useState<OnlyOfficeDocConfig | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [serverOffline, setServerOffline] = useState<boolean>(false);

  const fetchConfig = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setServerOffline(false);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/raw-documents/${encodeURIComponent(filename)}/onlyoffice-config`
      );
      if (!res.ok) {
        throw new Error(`Không thể lấy cấu hình OnlyOffice (Mã lỗi: ${res.status})`);
      }
      const data: OnlyOfficeDocConfig = await res.json();
      setConfig(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Lỗi kết nối máy chủ');
    } finally {
      setIsLoading(false);
    }
  }, [filename]);

  useEffect(() => {
    queueMicrotask(() => void fetchConfig());
  }, [fetchConfig]);

  const handleComponentError = (errorCode: number, errorDescription: string) => {
    // errorCode -2: Failed to load DocsAPI from documentServerUrl
    // errorCode -3: DocsAPI loaded but not defined on window
    setServerOffline(true);
    setError(`Không thể tải OnlyOffice Document Server [${errorCode}]: ${errorDescription}`);
  };

  if (isLoading) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-[#141312] border border-[#2d2b27] rounded-xl text-zinc-400 p-8 space-y-3">
        <Loader2 className="w-6 h-6 animate-spin text-[#d97757]" />
        <p className="text-xs">Đang nạp trình soạn thảo OnlyOffice...</p>
      </div>
    );
  }

  if (serverOffline || error) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-[#181715] border border-[#33302b] rounded-2xl p-8 text-center max-w-xl mx-auto my-auto shadow-lg">
        <div className="w-12 h-12 rounded-full bg-amber-950/40 border border-amber-800/50 flex items-center justify-center mb-4 text-amber-400">
          <AlertCircle className="w-6 h-6" />
        </div>
        <h3 className="text-sm font-semibold text-white tracking-tight">
          Không thể kết nối máy chủ OnlyOffice
        </h3>
        <p className="text-xs text-zinc-400 mt-2 leading-5">
          Máy chủ soạn thảo Word ({config?.documentServerUrl || 'ONLYOFFICE_SERVER_URL'}) hiện chưa phản hồi hoặc chưa được khởi chạy.
        </p>

        <div className="flex items-center gap-3 mt-6">
          <button
            onClick={() => void fetchConfig()}
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

  if (!config) {
    return null;
  }

  const editorId = `onlyoffice_editor_${filename.replace(/[^a-zA-Z0-9]/g, '_')}`;

  return (
    <div className="w-full h-full rounded-xl overflow-hidden border border-[#2d2b27] bg-[#141312] shadow-inner relative">
      <DocumentEditor
        id={editorId}
        documentServerUrl={config.documentServerUrl}
        config={
          {
            document: config.document,
            documentType: config.documentType,
            editorConfig: config.editorConfig,
          } as unknown as DocumentEditorProps['config']
        }
        height="100%"
        width="100%"
        onLoadComponentError={handleComponentError}
      />
    </div>
  );
};

export default OnlyOfficeDocEditor;
