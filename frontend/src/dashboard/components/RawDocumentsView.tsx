import React, { useState, useEffect, useRef } from 'react';
import {
  Search,
  BookOpen,
  FileText,
  Download,
  ExternalLink,
  Calendar,
  HardDrive,
  AlertCircle,
  FileCode,
  Layers,
  FileSpreadsheet,
  Filter,
  Plus,
  Loader2,
  Trash2
} from 'lucide-react';
import { API_BASE_URL } from '../../lib/apiConfig';
import { DocxViewer } from './DocxViewer';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export interface RawDocumentItem {
  filename: string;
  file_type: string;
  size: number;
  updated_at: string;
  has_extracted_md: boolean;
  extracted_md_name?: string | null;
}

export const RawDocumentsView: React.FC = () => {
  const [documents, setDocuments] = useState<RawDocumentItem[]>([]);
  const [selectedDoc, setSelectedDoc] = useState<RawDocumentItem | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<'all' | 'pdf' | 'docx'>('all');
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'preview' | 'extracted'>('preview');
  const [extractedContent, setExtractedContent] = useState<string | null>(null);
  const [isExtractLoading, setIsExtractLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchDocuments = async (selectDocName?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/raw-documents`);
      if (!res.ok) throw new Error('Không thể tải danh sách tài liệu quy trình');
      const data: RawDocumentItem[] = await res.json();
      const sorted = [...data].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      );
      setDocuments(sorted);
      if (selectDocName) {
        const found = sorted.find((d) => d.filename === selectDocName);
        if (found) setSelectedDoc(found);
      } else if (sorted.length > 0 && !selectedDoc) {
        setSelectedDoc(sorted[0]);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Lỗi kết nối máy chủ');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    queueMicrotask(() => void fetchDocuments());
    // Initial load only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch(`${API_BASE_URL}/api/v1/raw-documents/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Không thể tải tệp lên');
      }

      const uploadedDoc: RawDocumentItem = await res.json();
      await fetchDocuments(uploadedDoc.filename);
      setSelectedDoc(uploadedDoc);
      setViewMode('preview');
      setExtractedContent(null);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Lỗi khi tải tệp lên');
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleDeleteDoc = async (filename: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    if (!window.confirm(`Bạn có chắc chắn muốn xóa tài liệu "${filename}"?`)) {
      return;
    }

    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/raw-documents/${encodeURIComponent(filename)}`,
        { method: 'DELETE' }
      );
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Không thể xóa tài liệu');
      }

      const nextDocs = documents.filter((d) => d.filename !== filename);
      setDocuments(nextDocs);
      if (selectedDoc?.filename === filename) {
        setSelectedDoc(nextDocs.length > 0 ? nextDocs[0] : null);
        setViewMode('preview');
        setExtractedContent(null);
      }
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Lỗi khi xóa tài liệu');
    }
  };

  const loadExtractedText = async (filename: string) => {
    setIsExtractLoading(true);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/v1/raw-documents/${encodeURIComponent(filename)}/extracted`
      );
      if (res.ok) {
        const data = await res.json();
        setExtractedContent(data.content || '');
      } else {
        setExtractedContent(null);
      }
    } catch {
      setExtractedContent(null);
    } finally {
      setIsExtractLoading(false);
    }
  };

  const handleSelectDoc = (doc: RawDocumentItem) => {
    setSelectedDoc(doc);
    setExtractedContent(null);
    setViewMode('preview'); // Luôn mặc định mở preview, chỉ khi bấm Xem trích xuất mới tải Markdown
  };

  const handleToggleExtracted = () => {
    if (viewMode === 'preview') {
      setViewMode('extracted');
      if (selectedDoc && !extractedContent) {
        void loadExtractedText(selectedDoc.filename);
      }
    } else {
      setViewMode('preview');
    }
  };

  const filteredDocs = documents.filter((doc) => {
    const query = searchQuery.toLowerCase();
    const matchesQuery =
      doc.filename.toLowerCase().includes(query) ||
      doc.file_type.toLowerCase().includes(query);
    if (!matchesQuery) return false;

    if (filterType === 'pdf') return doc.file_type === 'pdf';
    if (filterType === 'docx') return doc.file_type === 'docx' || doc.file_type === 'doc';
    return true;
  });

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const formatDate = (isoStr: string) => {
    try {
      const date = new Date(isoStr);
      return date.toLocaleString('vi-VN', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoStr;
    }
  };

  const getDocUrl = (filename: string) =>
    `${API_BASE_URL}/api/v1/raw-documents/${encodeURIComponent(filename)}`;

  const getFileIcon = (fileType: string) => {
    switch (fileType.toLowerCase()) {
      case 'pdf':
        return <FileText className="w-4 h-4 text-rose-400 shrink-0" />;
      case 'docx':
      case 'doc':
        return <FileSpreadsheet className="w-4 h-4 text-blue-400 shrink-0" />;
      default:
        return <FileCode className="w-4 h-4 text-[#d97757] shrink-0" />;
    }
  };

  return (
    <div className="flex-1 flex h-full bg-[#1b1a17] text-[#f3f2ef] overflow-hidden">
      {/* SIDEBAR FILE LIST (LEFT) */}
      <aside className="w-[320px] border-r border-[#2d2b27] flex flex-col bg-[#1c1b18] shrink-0">
        <div className="p-4 border-b border-[#2d2b27] flex flex-col gap-3">
          {/* HEADER: TITLE & '+' UPLOAD BUTTON */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BookOpen className="w-5 h-5 text-[#d97757]" />
              <h2 className="text-sm font-semibold tracking-tight">Tài liệu quy trình</h2>
            </div>

            {/* '+' UPLOAD ACTION BUTTON */}
            <div className="flex items-center">
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileUpload}
                accept=".pdf,.docx,.doc"
                className="hidden"
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
                className="p-1.5 rounded-lg hover:bg-[#252320] text-zinc-400 hover:text-white transition-colors cursor-pointer disabled:opacity-50"
                title="Tải lên tài liệu quy trình (.pdf, .docx)"
              >
                {isUploading ? (
                  <Loader2 className="w-4 h-4 animate-spin text-[#d97757]" />
                ) : (
                  <Plus className="w-4 h-4" />
                )}
              </button>
            </div>
          </div>

          {/* SEARCH BAR & COMPACT FILTER ON THE SAME ROW */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-2.5 w-4 h-4 text-zinc-500" />
              <input
                type="text"
                placeholder="Tìm kiếm tài liệu..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-3 py-1.5 text-xs rounded-xl bg-[#252320] border border-[#33312d] text-[#f3f2ef] placeholder-zinc-500 focus:outline-none focus:border-[#d97757]/50"
              />
            </div>
            <div className="relative shrink-0 flex items-center">
              <Filter className="absolute left-2 w-3.5 h-3.5 text-zinc-500 pointer-events-none" />
              <select
                aria-label="Lọc loại tệp"
                value={filterType}
                onChange={(e) => setFilterType(e.target.value as 'all' | 'pdf' | 'docx')}
                className="pl-7 pr-3 py-1.5 text-xs rounded-xl bg-[#252320] border border-[#33312d] text-zinc-300 focus:outline-none focus:border-[#d97757]/50 cursor-pointer appearance-none"
              >
                <option value="all">Tất cả</option>
                <option value="pdf">PDF</option>
                <option value="docx">DOCX</option>
              </select>
            </div>
          </div>
        </div>

        {/* DOCUMENT LIST */}
        <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-1">
          {isLoading && documents.length === 0 ? (
            <div className="p-4 text-center text-xs text-zinc-500">Đang tải tài liệu...</div>
          ) : error ? (
            <div className="p-4 text-center text-xs text-rose-400">{error}</div>
          ) : filteredDocs.length === 0 ? (
            <div className="p-4 text-center text-xs text-zinc-500">Không tìm thấy tài liệu phù hợp</div>
          ) : (
            filteredDocs.map((doc) => {
              const isSelected = selectedDoc?.filename === doc.filename;
              return (
                <div
                  key={doc.filename}
                  onClick={() => handleSelectDoc(doc)}
                  className={`group p-3 rounded-xl cursor-pointer transition-all duration-200 border flex items-center justify-between gap-2 ${
                    isSelected
                      ? 'bg-[#292724] border-[#d97757]/40 shadow-sm'
                      : 'border-transparent hover:bg-[#23211e] text-zinc-300'
                  }`}
                >
                  <div className="flex items-start gap-2.5 min-w-0 flex-1">
                    {getFileIcon(doc.file_type)}
                    <div className="flex-1 min-w-0">
                      <p
                        className={`text-xs font-medium truncate ${
                          isSelected ? 'text-[#f3f2ef]' : 'text-zinc-300'
                        }`}
                        title={doc.filename}
                      >
                        {doc.filename}
                      </p>
                      <div className="flex items-center gap-3 text-[11px] text-zinc-500 mt-1">
                        <span>{formatSize(doc.size)}</span>
                      </div>
                    </div>
                  </div>

                  {/* NÚT XÓA GIỐNG ARTIFACTS */}
                  <button
                    onClick={(e) => handleDeleteDoc(doc.filename, e)}
                    className="p-1.5 rounded-lg opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-rose-400 hover:bg-[#2e2b27] transition-all cursor-pointer shrink-0"
                    title="Xóa tài liệu"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })
          )}
        </div>

        {/* FOOTER METRICS */}
        <div className="p-3 bg-[#171614] border-t border-[#2d2b27] text-[11px] text-zinc-500 flex justify-between">
          <span>Nguồn: data/raw</span>
          <span>{documents.length} tệp</span>
        </div>
      </aside>

      {/* MAIN PREVIEW / EXTRACT AREA (RIGHT) */}
      <main className="flex-1 flex flex-col h-full overflow-hidden bg-[#181715]">
        {selectedDoc ? (
          <div className="flex-1 flex flex-col h-full overflow-hidden">
            {/* TOP ACTIONS HEADER (CONSISTENT FOR ALL FILES) */}
            <div className="px-6 py-3.5 border-b border-[#2d2b27] bg-[#1c1b18] flex flex-wrap items-center justify-between gap-4 shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-2 rounded-lg bg-[#252320] border border-[#33312d]">
                  {getFileIcon(selectedDoc.file_type)}
                </div>
                <div className="min-w-0">
                  <h1 className="text-sm font-semibold text-white truncate max-w-md sm:max-w-xl" title={selectedDoc.filename}>
                    {selectedDoc.filename}
                  </h1>
                  <div className="flex items-center gap-4 text-xs text-zinc-400 mt-0.5">
                    <span className="flex items-center gap-1">
                      <HardDrive className="w-3 h-3 text-zinc-500" />
                      {formatSize(selectedDoc.size)}
                    </span>
                    <span className="flex items-center gap-1">
                      <Calendar className="w-3 h-3 text-zinc-500" />
                      {formatDate(selectedDoc.updated_at)}
                    </span>
                  </div>
                </div>
              </div>

              {/* ACTION BUTTONS */}
              <div className="flex items-center gap-2">
                {/* TOGGLE EXTRACTED MARKDOWN (CHỈ TẢI KHI BẤM) */}
                {selectedDoc.has_extracted_md && (
                  <button
                    onClick={handleToggleExtracted}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors cursor-pointer border ${
                      viewMode === 'extracted'
                        ? 'bg-[#292724] text-[#d97757] border-[#d97757]/40'
                        : 'bg-[#252320] hover:bg-[#33302b] text-zinc-200 border-[#33312d]'
                    }`}
                  >
                    <Layers className="w-3.5 h-3.5 text-[#d97757]" />
                    <span>
                      {viewMode === 'extracted'
                        ? selectedDoc.file_type === 'docx' || selectedDoc.file_type === 'doc'
                          ? 'Xem văn bản Word'
                          : 'Xem bản gốc'
                        : 'Xem trích xuất'}
                    </span>
                  </button>
                )}

                <a
                  href={getDocUrl(selectedDoc.filename)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-[#2c2a26] hover:bg-[#383530] text-zinc-200 rounded-lg text-xs font-semibold transition-colors cursor-pointer border border-[#3e3b36]"
                  title="Mở tài liệu trong tab mới"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span>Mở tab mới</span>
                </a>

                <a
                  href={getDocUrl(selectedDoc.filename)}
                  download={selectedDoc.filename}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 bg-[#d97757] hover:bg-[#e08862] text-[#1c1b18] rounded-lg text-xs font-semibold transition-colors cursor-pointer shadow-xs"
                  title="Tải tệp xuống"
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Tải xuống</span>
                </a>

                {/* TOP DELETE BUTTON */}
                <button
                  onClick={() => handleDeleteDoc(selectedDoc.filename)}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-[#2c2a26] hover:bg-rose-950/40 hover:text-rose-400 text-zinc-400 rounded-lg text-xs font-semibold transition-colors cursor-pointer border border-[#3e3b36] hover:border-rose-900/50"
                  title="Xóa tài liệu"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Xóa</span>
                </button>
              </div>
            </div>

            {/* PREVIEW CONTAINER */}
            <div className="flex-1 overflow-hidden p-4">
              {viewMode === 'preview' && selectedDoc.file_type === 'pdf' ? (
                /* PDF VIEWER EMBED */
                <div className="w-full h-full rounded-xl overflow-hidden border border-[#2d2b27] bg-[#141312] shadow-inner">
                  <iframe
                    src={getDocUrl(selectedDoc.filename)}
                    title={`pdf-preview-${selectedDoc.filename}`}
                    className="w-full h-full border-0 bg-[#2b2b2b]"
                  />
                </div>
              ) : viewMode === 'preview' &&
                (selectedDoc.file_type === 'docx' || selectedDoc.file_type === 'doc') ? (
                /* DOCX VIEWER EMBED */
                <DocxViewer
                  filename={selectedDoc.filename}
                  onFallbackToMarkdown={() => {
                    setViewMode('extracted');
                    if (!extractedContent) {
                      void loadExtractedText(selectedDoc.filename);
                    }
                  }}
                />
              ) : viewMode === 'extracted' ? (
                /* MARKDOWN EXTRACTED PREVIEW (CHỈ HIỆN KHI VIEWMODE === 'extracted') */
                <div className="h-full overflow-y-auto custom-scrollbar">
                  <div className="max-w-4xl mx-auto">
                    <div className="bg-[#1c1b18] border border-[#2d2b27] rounded-2xl p-6 shadow-sm font-sans">
                      {isExtractLoading ? (
                        <div className="py-12 text-center text-xs text-zinc-500">
                          Đang tải văn bản trích xuất...
                        </div>
                      ) : extractedContent ? (
                        <DocMarkdownRenderer text={extractedContent} />
                      ) : (
                        <div className="py-12 text-center space-y-2">
                          <p className="text-xs text-zinc-400">
                            Không có văn bản trích xuất cho tài liệu này.
                          </p>
                          <a
                            href={getDocUrl(selectedDoc.filename)}
                            download={selectedDoc.filename}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#d97757] text-[#1c1b18] rounded-lg text-xs font-semibold"
                          >
                            <Download className="w-3.5 h-3.5" />
                            <span>Tải tệp gốc</span>
                          </a>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                /* FALLBACK VIEW */
                <div className="w-full h-full flex flex-col items-center justify-center bg-[#141312] border border-[#2d2b27] rounded-xl text-zinc-400 p-8 space-y-3">
                  <AlertCircle className="w-8 h-8 text-zinc-500" />
                  <p className="text-sm font-medium text-zinc-300">Không có bản xem trước cho định dạng này</p>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-zinc-500">
            <BookOpen className="w-12 h-12 mb-3 text-zinc-600 stroke-[1.5]" />
            <h3 className="text-sm font-semibold text-zinc-300">Chưa chọn tài liệu</h3>
            <p className="text-xs text-zinc-500 max-w-sm mt-1">
              Chọn một tài liệu quy trình từ thanh bên để xem trước trực tiếp PDF, Word hoặc đọc văn bản trích xuất.
            </p>
          </div>
        )}
      </main>
    </div>
  );
};

export default RawDocumentsView;

// Standard GFM Markdown Renderer with dark theme styles
const DocMarkdownRenderer: React.FC<{ text: string }> = ({ text }) => {
  return (
    <div className="text-zinc-200 text-xs sm:text-sm leading-relaxed selection:bg-[#d97757]/30 selection:text-white space-y-3">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="text-lg sm:text-xl font-bold text-white pt-4 pb-1 border-b border-[#2d2b27] first:pt-0 mb-2">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-base sm:text-lg font-semibold text-[#f3f2ef] pt-3 pb-0.5 mb-2">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm sm:text-base font-semibold text-[#e89b82] pt-2 mb-1">
              {children}
            </h3>
          ),
          h4: ({ children }) => (
            <h4 className="text-xs sm:text-sm font-semibold text-zinc-200 pt-1.5 mb-1">
              {children}
            </h4>
          ),
          h5: ({ children }) => (
            <h5 className="text-xs font-semibold text-zinc-300 pt-1 mb-1">
              {children}
            </h5>
          ),
          h6: ({ children }) => (
            <h6 className="text-xs font-medium text-zinc-400 pt-1 mb-1">
              {children}
            </h6>
          ),
          p: ({ children }) => (
            <p className="text-zinc-300 mb-2 leading-relaxed last:mb-0">{children}</p>
          ),
          ul: ({ children }) => (
            <ul className="list-disc list-outside pl-5 space-y-1.5 my-2 text-zinc-300">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal list-outside pl-5 space-y-1.5 my-2 text-zinc-300">
              {children}
            </ol>
          ),
          li: ({ children }) => (
            <li className="pl-1 text-zinc-300 leading-relaxed [&>p]:inline [&>p]:m-0 [&>p+p]:block [&>p+p]:mt-1.5">
              {children}
            </li>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-[#d97757] pl-3 py-1 bg-[#23211e] rounded-r-lg text-zinc-400 italic my-2">
              {children}
            </blockquote>
          ),
          code: ({ children, className }) => {
            const isInline = !className;
            return isInline ? (
              <code className="bg-[#252320] text-[#e89b82] px-1.5 py-0.5 rounded text-[11px] font-mono">
                {children}
              </code>
            ) : (
              <pre className="bg-[#141312] border border-[#2d2b27] rounded-xl p-3 overflow-x-auto my-3 text-xs font-mono text-zinc-200">
                <code>{children}</code>
              </pre>
            );
          },
          table: ({ children }) => (
            <div className="overflow-x-auto my-3">
              <table className="w-full border-collapse border border-[#2d2b27] text-xs">
                {children}
              </table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-[#2d2b27] bg-[#252320] px-3 py-1.5 text-left font-semibold text-white">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-[#2d2b27] px-3 py-1.5 text-zinc-300">
              {children}
            </td>
          ),
          hr: () => <hr className="border-[#2d2b27] my-4" />,
          strong: ({ children }) => (
            <strong className="font-semibold text-white">{children}</strong>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[#d97757] hover:underline"
            >
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
};
