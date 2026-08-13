import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FileText, LoaderCircle, Trash2, Upload, X } from 'lucide-react';
import {
  deleteProjectDocument,
  listProjectDocuments,
  uploadProjectDocument,
} from './api';
import type { ProjectDocument } from './api';

interface Props {
  projectId: string;
  projectName?: string;
}

const ACTIVE = new Set(['received', 'extracting', 'indexing']);

export const ProjectDocumentPanel: React.FC<Props> = ({ projectId, projectName }) => {
  const [open, setOpen] = useState(false);
  const [documents, setDocuments] = useState<ProjectDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      setDocuments(await listProjectDocuments(projectId));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Document list unavailable.');
    }
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
  }, [refresh]);

  useEffect(() => {
    if (!open || !documents.some((item) => ACTIVE.has(item.status))) return;
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [documents, open, refresh]);

  const upload = async (files: File[]) => {
    setUploading(true);
    try {
      for (const file of files) await uploadProjectDocument(projectId, file);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed.');
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={!projectId}
        className="fixed right-5 top-16 z-30 flex items-center gap-2 rounded-lg border border-[#3b3833] bg-[#242320] px-3 py-2 text-xs text-zinc-300 shadow-xl hover:bg-[#302e2a] disabled:opacity-40"
      >
        <FileText className="h-4 w-4" /> Project documents
      </button>
      {open && (
        <aside
          role="dialog"
          aria-label="Project documents"
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-[#3b3833] bg-[#1f1e1b] shadow-2xl"
        >
          <header className="flex items-center justify-between border-b border-[#37342f] p-4">
            <div>
              <h2 className="font-semibold text-zinc-100">Project documents</h2>
              <p className="text-xs text-zinc-500">{projectName ?? 'Active Project'}</p>
            </div>
            <button aria-label="Close project documents" onClick={() => setOpen(false)}>
              <X className="h-5 w-5 text-zinc-400" />
            </button>
          </header>
          <div className="border-b border-[#37342f] p-4">
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              disabled={uploading}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#d97757] px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {uploading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              Upload PDF or DOCX
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
            <p className="mt-2 text-center text-[11px] text-zinc-500">25 MiB/file · retained for 30 days</p>
          </div>
          <div className="flex-1 space-y-2 overflow-y-auto p-4">
            {error && <p role="alert" className="rounded-lg bg-rose-950/40 p-3 text-xs text-rose-300">{error}</p>}
            {!error && documents.length === 0 && (
              <p className="py-10 text-center text-sm text-zinc-500">No documents in this Project.</p>
            )}
            {documents.map((document) => (
              <div key={document.document_id} className="rounded-xl border border-[#38352f] bg-[#272521] p-3">
                <div className="flex items-start gap-3">
                  <FileText className="mt-0.5 h-4 w-4 shrink-0 text-[#d97757]" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-zinc-100">{document.title}</p>
                    <p className="mt-1 text-xs text-zinc-500">
                      {ACTIVE.has(document.status) ? 'Processing — not ready yet' : document.status}
                      {document.status === 'ready' && ` · ${document.page_count} pages`}
                    </p>
                    {document.reason_code && <p className="mt-1 text-xs text-rose-300">{document.reason_code}</p>}
                  </div>
                  {ACTIVE.has(document.status) ? (
                    <LoaderCircle className="h-4 w-4 animate-spin text-zinc-500" />
                  ) : (
                    <button
                      aria-label={`Delete ${document.title}`}
                      onClick={() => {
                        if (!window.confirm(`Delete ${document.title}?`)) return;
                        void deleteProjectDocument(projectId, document.document_id).then(refresh);
                      }}
                    >
                      <Trash2 className="h-4 w-4 text-zinc-500 hover:text-rose-300" />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </aside>
      )}
    </>
  );
};
