import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FileText, LoaderCircle, Trash2, Upload, X } from 'lucide-react';
import {
  DEFAULT_POLL_TIMEOUT_MS,
  deleteProjectDocument,
  listProjectDocuments,
  uploadProjectDocument,
} from './api';
import type { ProjectDocument } from './api';
import { documentError, documentLocale, documentText } from './i18n';

interface Props {
  projectId: string;
  projectName?: string;
  isOpen?: boolean;
  onClose?: () => void;
  hideTrigger?: boolean;
}

const ACTIVE = new Set(['received', 'extracting', 'indexing', 'deleting']);

export const ProjectDocumentPanel: React.FC<Props> = ({
  projectId,
  projectName,
  isOpen: controlledIsOpen,
  onClose,
  hideTrigger = false,
}) => {
  const [internalOpen, setInternalOpen] = useState(false);
  const isControlled = controlledIsOpen !== undefined;
  const open = isControlled ? controlledIsOpen : internalOpen;

  const setOpen = useCallback((nextOpen: boolean) => {
    if (!nextOpen && onClose) {
      onClose();
    }
    if (!isControlled) {
      setInternalOpen(nextOpen);
    }
  }, [isControlled, onClose]);
  const [documents, setDocuments] = useState<ProjectDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const pollingStartedAtRef = useRef<number | null>(null);
  const refreshAbortRef = useRef<AbortController | null>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    refreshAbortRef.current?.abort();
    const controller = new AbortController();
    refreshAbortRef.current = controller;
    try {
      setDocuments(await listProjectDocuments(projectId, controller.signal));
      setError(null);
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : documentText('listUnavailable'));
    } finally {
      if (refreshAbortRef.current === controller) refreshAbortRef.current = null;
    }
  }, [projectId]);

  useEffect(() => () => {
    refreshAbortRef.current?.abort();
    uploadAbortRef.current?.abort();
  }, [projectId]);

  useEffect(() => {
    queueMicrotask(() => {
      setDocuments([]);
      if (open) void refresh();
    });
  }, [open, projectId, refresh]);

  useEffect(() => {
    const openPanel = () => { setOpen(true); void refresh(); };
    const updated = () => void refresh();
    window.addEventListener('open-project-documents', openPanel);
    window.addEventListener('project-documents-updated', updated);
    return () => {
      window.removeEventListener('open-project-documents', openPanel);
      window.removeEventListener('project-documents-updated', updated);
    };
  }, [refresh, setOpen]);

  useEffect(() => {
    if (!open || !documents.some((item) => ACTIVE.has(item.status))) {
      pollingStartedAtRef.current = null;
      return;
    }
    pollingStartedAtRef.current ??= Date.now();
    const remaining = DEFAULT_POLL_TIMEOUT_MS - (Date.now() - pollingStartedAtRef.current);
    const timer = window.setTimeout(() => {
      if (remaining <= 2_000) {
        setError(documentText('processingTimeout'));
        return;
      }
      void refresh();
    }, Math.min(2_000, Math.max(0, remaining)));
    return () => window.clearTimeout(timer);
  }, [documents, open, refresh]);

  useEffect(() => {
    pollingStartedAtRef.current = null;
  }, [projectId]);

  const upload = async (files: File[]) => {
    uploadAbortRef.current?.abort();
    const controller = new AbortController();
    uploadAbortRef.current = controller;
    setUploading(true);
    try {
      for (const file of files) {
        await uploadProjectDocument(projectId, file, undefined, controller.signal);
      }
      await refresh();
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : documentText('uploadFailed'));
    } finally {
      if (uploadAbortRef.current === controller) {
        uploadAbortRef.current = null;
        setUploading(false);
      }
    }
  };

  const remove = async (document: ProjectDocument) => {
    setDocuments((current) => current.map((item) =>
      item.document_id === document.document_id ? { ...item, status: 'deleting' } : item
    ));
    try {
      await deleteProjectDocument(projectId, document.document_id);
      await refresh();
    } catch (cause) {
      await refresh();
      setError(cause instanceof Error ? cause.message : documentText('listUnavailable'));
    }
  };

  return (
    <>
      {!hideTrigger && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          disabled={!projectId}
          className="fixed right-5 top-16 z-30 flex items-center gap-2 rounded-lg border border-[#3b3833] bg-[#242320] px-3 py-2 text-xs text-zinc-300 shadow-xl hover:bg-[#302e2a] disabled:opacity-40"
        >
          <FileText className="h-4 w-4" /> {documentText('title')}
        </button>
      )}
      <aside
        role="dialog"
        aria-label={documentText('title')}
        aria-hidden={!open}
        className={`min-h-0 flex flex-col transition-all duration-300 ease-in-out overflow-hidden z-20 shrink-0 border-[#3b3833] bg-[#1f1e1b] ${
          open
            ? 'w-[380px] sm:w-[420px] max-w-[90vw] border-l opacity-100'
            : 'w-0 border-l-0 opacity-0 pointer-events-none'
        }`}
      >
        <div className="w-[380px] sm:w-[420px] max-w-[90vw] h-full flex flex-col min-h-0 overflow-hidden">
          <header className="flex items-center justify-between border-b border-[#37342f] p-4 shrink-0">
            <div>
              <h2 className="font-semibold text-zinc-100">{documentText('title')}</h2>
              <p className="text-xs text-zinc-500">{projectName ?? 'Active Project'}</p>
            </div>
            <button aria-label="Close project documents" onClick={() => setOpen(false)}>
              <X className="h-5 w-5 text-zinc-400 hover:text-zinc-200 cursor-pointer" />
            </button>
          </header>
          <div className="border-b border-[#37342f] p-4 shrink-0">
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              disabled={uploading}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#d97757] px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50 cursor-pointer hover:bg-[#e08862] transition-colors"
            >
              {uploading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              {documentText('upload')}
            </button>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".pdf,.docx"
              className="sr-only"
              onChange={(event) => {
                void upload(Array.from(event.target.files ?? []));
                event.target.value = '';
              }}
            />
            <p className="mt-2 text-center text-[11px] text-zinc-500">{documentText('retention')}</p>
          </div>
          <div className="flex-1 space-y-2 overflow-y-auto p-4 custom-scrollbar">
            {error && <p role="alert" className="rounded-lg bg-rose-950/40 p-3 text-xs text-rose-300">{error}</p>}
            {!error && documents.length === 0 && (
              <p className="py-10 text-center text-sm text-zinc-500">{documentText('empty')}</p>
            )}
            {documents.map((document) => (
              <div key={document.document_id} className="rounded-xl border border-[#38352f] bg-[#272521] p-3">
                <div className="flex items-start gap-3">
                  <FileText className="mt-0.5 h-4 w-4 shrink-0 text-[#d97757]" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-zinc-100">{document.filename}</p>
                    <p className="mt-1 text-xs text-zinc-500">
                      {document.status === 'deleting'
                        ? documentText('deleting')
                        : ACTIVE.has(document.status)
                          ? documentText('processing')
                          : document.status}
                      {document.status === 'ready' && ` · ${document.page_count} ${documentText('pages')} · ${document.chunk_count} ${documentText('chunks')}`}
                    </p>
                    <p className="mt-1 text-[11px] text-zinc-600">
                      {documentText('expires')}: {new Date(document.expires_at).toLocaleDateString(documentLocale())}
                    </p>
                    {document.error_code && <p className="mt-1 text-xs text-rose-300">{documentError(document.error_code)}</p>}
                  </div>
                  <div className="flex items-center gap-2">
                    {ACTIVE.has(document.status) && (
                      <LoaderCircle className="h-4 w-4 animate-spin text-zinc-500" />
                    )}
                    {document.status !== 'deleting' && document.status !== 'deleted' && (
                      <button
                        aria-label={`Delete ${document.filename}`}
                        onClick={() => {
                          if (!window.confirm(`Delete ${document.filename}?`)) return;
                          void remove(document);
                        }}
                      >
                        <Trash2 className="h-4 w-4 text-zinc-500 hover:text-rose-300 cursor-pointer" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </aside>
    </>
  );
};
