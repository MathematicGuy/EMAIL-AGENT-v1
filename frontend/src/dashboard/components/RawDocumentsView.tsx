import React, { useState, useEffect } from 'react';
import {
  Search,
  BookOpen,
  FileText,
  Download,
  ExternalLink,
  Calendar,
  HardDrive,
  AlertCircle,
  FileCheck2,
  FileCode,
  Layers,
  FileSpreadsheet,
  Filter
} from 'lucide-react';
import { API_BASE_URL } from '../../lib/apiConfig';
import { OnlyOfficeDocEditor } from './OnlyOfficeDocEditor';

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
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'preview' | 'extracted'>('preview');
  const [extractedContent, setExtractedContent] = useState<string | null>(null);
  const [extractedFor, setExtractedFor] = useState<string | null>(null);
  const [isExtractLoading, setIsExtractLoading] = useState(false);

  const fetchDocuments = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/raw-documents`);
      if (!res.ok) throw new Error('Could not load process documents');
      const data: RawDocumentItem[] = await res.json();
      setDocuments(data);
      if (data.length > 0 && !selectedDoc) {
        setSelectedDoc(data[0]);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Server connection error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    queueMicrotask(() => void fetchDocuments());
    // Initial load only; later refreshes are explicit events.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadExtractedText = async (filename: string) => {
    setIsExtractLoading(true);
    setExtractedFor(filename);
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
    setExtractedFor(null);
    setViewMode('preview');
  };

  const handleToggleExtracted = () => {
    setViewMode((mode) => (mode === 'preview' ? 'extracted' : 'preview'));
  };

  // The extracted pane is reached three ways: the toggle, the OnlyOffice fallback
  // button, and simply selecting a non-PDF/non-Word file that has extracted text.
  // Deriving one flag and loading from an effect keeps "is the pane visible" and
  // "did we fetch" from drifting apart -- the toggle-only trigger used to leave the
  // third path showing "no extracted text" for documents that had it.
  const isPdf = selectedDoc?.file_type === 'pdf';
  const isWordDoc = selectedDoc?.file_type === 'docx' || selectedDoc?.file_type === 'doc';
  const showsExtractedPane =
    !!selectedDoc &&
    !(viewMode === 'preview' && (isPdf || isWordDoc)) &&
    (viewMode === 'extracted' || selectedDoc.has_extracted_md);

  useEffect(() => {
    if (!selectedDoc || !showsExtractedPane) return;
    if (!selectedDoc.has_extracted_md) return;
    // extractedFor is set before the request resolves, so a failed fetch reports
    // "no extracted text" instead of retrying on every render.
    if (extractedFor === selectedDoc.filename) return;
    void loadExtractedText(selectedDoc.filename);
  }, [selectedDoc, showsExtractedPane, extractedFor]);

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
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BookOpen className="w-5 h-5 text-[#d97757]" />
              <h2 className="text-sm font-semibold tracking-tight">Tài liệu quy trình</h2>
            </div>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-[#2a2824] text-zinc-400 border border-[#383530]">
              {documents.length}
            </span>
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
                className="pl-6 pr-2 py-1.5 text-xs rounded-xl bg-[#252320] border border-[#33312d] text-zinc-300 hover:text-white focus:outline-none focus:border-[#d97757]/50 cursor-pointer font-medium"
              >
                <option value="all">Tất cả</option>
                <option value="pdf">PDF</option>
                <option value="docx">DOCX</option>
              </select>
            </div>
          </div>
        </div>

        {/* FILE LIST */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1 custom-scrollbar">
          {isLoading && documents.length === 0 ? (
            <div className="text-center py-8 text-xs text-zinc-500">Đang tải tài liệu...</div>
          ) : error ? (
            <div className="p-3 text-center text-xs text-rose-400 bg-rose-950/20 rounded-xl border border-rose-900/30 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : filteredDocs.length === 0 ? (
            <div className="text-center py-8 text-xs text-zinc-500">
              {searchQuery ? 'Không tìm thấy tài liệu phù hợp' : 'Chưa có tài liệu nào'}
            </div>
          ) : (
            filteredDocs.map((doc) => {
              const isSelected = selectedDoc?.filename === doc.filename;
              return (
                <div
                  key={doc.filename}
                  onClick={() => handleSelectDoc(doc)}
                  className={`group flex items-center justify-between p-2.5 rounded-xl cursor-pointer transition-all duration-150 ${
                    isSelected
                      ? 'bg-[#d97757]/15 border border-[#d97757]/40 text-white font-medium shadow-xs'
                      : 'border border-transparent hover:bg-[#252320] text-zinc-300 hover:text-white'
                  }`}
                >
                  <div className="flex items-center gap-2.5 min-w-0 flex-1">
                    {getFileIcon(doc.file_type)}
                    <div className="min-w-0 flex-1">
                      <p className="text-xs truncate font-mono">{doc.filename}</p>
                      <div className="flex items-center gap-2 mt-0.5 text-[10px] text-zinc-500">
                        <span>{formatSize(doc.size)}</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* FOOTER STATS */}
        <div className="p-3 border-t border-[#2d2b27] bg-[#171614] text-[10px] text-zinc-500 flex items-center justify-between">
          <div className="flex items-center gap-1.5 truncate">
            <HardDrive className="w-3 h-3 text-zinc-400 shrink-0" />
            <span className="truncate">Nguồn: data/raw</span>
          </div>
          <span className="shrink-0">{filteredDocs.length} tệp</span>
        </div>
      </aside>

      {/* MAIN VIEWER (RIGHT) */}
      <main className="flex-1 flex flex-col bg-[#1b1a17] overflow-hidden">
        {selectedDoc ? (
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* TOOLBAR */}
            <div className="px-6 py-3.5 border-b border-[#2d2b27] bg-[#1c1b18] flex items-center justify-between shrink-0">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2.5">
                  {getFileIcon(selectedDoc.file_type)}
                  <h1 className="text-sm font-semibold text-white tracking-tight truncate font-mono">
                    {selectedDoc.filename}
                  </h1>
                </div>
                <p className="text-[10px] text-zinc-500 mt-1 flex items-center gap-3">
                  <span className="flex items-center gap-1">
                    <HardDrive className="w-3 h-3" /> Dung lượng: {formatSize(selectedDoc.size)}
                  </span>
                  <span className="flex items-center gap-1">
                    <Calendar className="w-3 h-3" /> Cập nhật: {formatDate(selectedDoc.updated_at)}
                  </span>
                </p>
              </div>

              {/* ACTION BUTTONS */}
              <div className="flex items-center gap-2 shrink-0 ml-4">
                {selectedDoc.has_extracted_md && (
                  <button
                    onClick={handleToggleExtracted}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
                      viewMode === 'extracted'
                        ? 'bg-[#d97757]/20 text-[#d97757] border border-[#d97757]/40'
                        : 'bg-[#2c2a26] text-zinc-300 hover:text-white'
                    }`}
                  >
                    <Layers className="w-3.5 h-3.5" />
                    <span>
                      {viewMode === 'extracted'
                        ? selectedDoc.file_type === 'docx' || selectedDoc.file_type === 'doc'
                          ? 'Soạn thảo Word'
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
              </div>
            </div>

            {/* PREVIEW CONTAINER */}
            <div className="flex-1 overflow-hidden p-4">
              {viewMode === 'preview' && isPdf ? (
                /* PDF VIEWER EMBED */
                <div className="w-full h-full rounded-xl overflow-hidden border border-[#2d2b27] bg-[#141312] shadow-inner">
                  <iframe
                    src={getDocUrl(selectedDoc.filename)}
                    title={selectedDoc.filename}
                    className="w-full h-full border-0 bg-[#2b2b2b]"
                  />
                </div>
              ) : viewMode === 'preview' && isWordDoc ? (
                /* ONLYOFFICE DOC EDITOR EMBED */
                <OnlyOfficeDocEditor
                  filename={selectedDoc.filename}
                  onFallbackToMarkdown={() => setViewMode('extracted')}
                />
              ) : showsExtractedPane ? (
                /* MARKDOWN EXTRACTED PREVIEW */
                <div className="h-full overflow-y-auto custom-scrollbar">
                  <div className="max-w-4xl mx-auto">
                    <div className="bg-[#1c1b18] border border-[#2d2b27] rounded-2xl p-6 shadow-sm whitespace-pre-wrap font-sans">
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
                /* FALLBACK NON-PDF NO EXTRACT */
                <div className="h-full flex flex-col items-center justify-center text-center p-8">
                  <div className="w-16 h-16 rounded-full bg-[#252320] border border-[#33312d] flex items-center justify-center mb-4">
                    {getFileIcon(selectedDoc.file_type)}
                  </div>
                  <h3 className="text-sm font-semibold text-white tracking-tight">
                    {selectedDoc.filename}
                  </h3>
                  <p className="text-xs text-zinc-400 max-w-sm mt-2 leading-5">
                    Định dạng <span className="uppercase font-mono font-bold text-amber-400">.{selectedDoc.file_type}</span>.
                    Bạn có thể tải tệp này về hoặc xem văn bản trích xuất.
                  </p>
                  <a
                    href={getDocUrl(selectedDoc.filename)}
                    download={selectedDoc.filename}
                    className="mt-4 flex items-center gap-1.5 px-4 py-2 bg-[#d97757] text-[#1c1b18] rounded-lg text-xs font-semibold hover:bg-[#e08862] transition-colors"
                  >
                    <Download className="w-3.5 h-3.5" />
                    <span>Tải bản gốc</span>
                  </a>
                </div>
              )}
            </div>
          </div>
        ) : (
          /* EMPTY STATE */
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center bg-[#1b1a17]">
            <div className="w-16 h-16 rounded-full bg-[#252320] border border-[#33312d] flex items-center justify-center mb-4">
              <FileCheck2 className="w-8 h-8 text-zinc-500" />
            </div>
            <h3 className="text-sm font-semibold text-white tracking-tight">
              Chưa chọn tài liệu
            </h3>
            <p className="text-xs text-zinc-500 max-w-sm mt-2 leading-5">
              Chọn một tài liệu quy trình từ thanh bên để xem trước trực tiếp PDF hoặc đọc văn bản đã trích xuất.
            </p>
          </div>
        )}
      </main>
    </div>
  );
};

// Inline markdown formatting helper
const parseInlineDocMarkdown = (text: string): React.ReactNode[] => {
  const regex = /(\*\*.*?\*\*|\*.*?\*|`.*?`)/g;
  const parts = text.split(regex);
  return parts.map((part, idx) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={idx} className="text-white font-bold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith('*') && part.endsWith('*')) {
      return (
        <em key={idx} className="italic text-zinc-300">
          {part.slice(1, -1)}
        </em>
      );
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code
          key={idx}
          className="bg-[#131210] text-amber-500 px-1 py-0.5 rounded font-mono text-[11px]"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
};

interface ParsedBlock {
  type: 'code' | 'h1' | 'h2' | 'h3' | 'ul' | 'ol' | 'hr' | 'quote' | 'spacer' | 'paragraph';
  content: string;
}

function parseMarkdownBlocks(text: string): ParsedBlock[] {
  const lines = text.split('\n');
  const blocks: ParsedBlock[] = [];
  let inCode = false;
  let codeLines: string[] = [];

  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (inCode) {
        inCode = false;
        blocks.push({ type: 'code', content: codeLines.join('\n') });
        codeLines = [];
      } else {
        inCode = true;
      }
      continue;
    }

    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (line.startsWith('# ')) {
      blocks.push({ type: 'h1', content: line.substring(2) });
    } else if (line.startsWith('## ')) {
      blocks.push({ type: 'h2', content: line.substring(3) });
    } else if (line.startsWith('### ')) {
      blocks.push({ type: 'h3', content: line.substring(4) });
    } else if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
      blocks.push({ type: 'ul', content: line.trim().substring(2) });
    } else if (/^\d+\.\s/.test(line.trim())) {
      const match = line.trim().match(/^\d+\.\s(.*)/);
      blocks.push({ type: 'ol', content: match ? match[1] : line.trim() });
    } else if (line.trim() === '---' || line.trim() === '***') {
      blocks.push({ type: 'hr', content: '' });
    } else if (line.trim().startsWith('> ')) {
      blocks.push({ type: 'quote', content: line.trim().substring(2) });
    } else if (!line.trim()) {
      blocks.push({ type: 'spacer', content: '' });
    } else {
      blocks.push({ type: 'paragraph', content: line });
    }
  }

  if (inCode && codeLines.length > 0) {
    blocks.push({ type: 'code', content: codeLines.join('\n') });
  }

  return blocks;
}

const DocMarkdownRenderer: React.FC<{ text: string }> = ({ text }) => {
  const blocks = parseMarkdownBlocks(text);

  return (
    <div className="space-y-4 text-zinc-300">
      {blocks.map((block, idx) => {
        switch (block.type) {
          case 'code':
            return (
              <pre
                key={idx}
                className="bg-[#131210] border border-[#2d2b27] rounded-xl p-4 overflow-x-auto text-[11px] font-mono text-amber-100/90 my-3"
              >
                <code className="block">{block.content}</code>
              </pre>
            );
          case 'h1':
            return (
              <h1
                key={idx}
                className="text-lg font-bold text-white border-b border-[#2d2b27] pb-2 mt-6 mb-3"
              >
                {block.content}
              </h1>
            );
          case 'h2':
            return (
              <h2 key={idx} className="text-base font-semibold text-white mt-5 mb-2">
                {block.content}
              </h2>
            );
          case 'h3':
            return (
              <h3 key={idx} className="text-sm font-semibold text-white mt-4 mb-2">
                {block.content}
              </h3>
            );
          case 'ul':
            return (
              <ul key={idx} className="list-disc pl-5 space-y-1 my-1">
                <li className="text-xs">{parseInlineDocMarkdown(block.content)}</li>
              </ul>
            );
          case 'ol':
            return (
              <ol key={idx} className="list-decimal pl-5 space-y-1 my-1">
                <li className="text-xs">{parseInlineDocMarkdown(block.content)}</li>
              </ol>
            );
          case 'hr':
            return <hr key={idx} className="border-[#2d2b27] my-6" />;
          case 'quote':
            return (
              <blockquote
                key={idx}
                className="border-l-4 border-[#d97757] bg-[#252320] px-4 py-2 rounded-r-lg my-3 italic text-zinc-400"
              >
                {parseInlineDocMarkdown(block.content)}
              </blockquote>
            );
          case 'spacer':
            return <div key={idx} className="h-2" />;
          case 'paragraph':
          default:
            return (
              <p key={idx} className="text-xs leading-6 text-zinc-300">
                {parseInlineDocMarkdown(block.content)}
              </p>
            );
        }
      })}
    </div>
  );
};

export default RawDocumentsView;
