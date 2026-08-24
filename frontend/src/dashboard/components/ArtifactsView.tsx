import React, { useState, useEffect } from 'react';
import {
  Search,
  Trash2,
  Plus,
  FileText,
  Edit2,
  Eye,
  Save,
  FileCode,
  Folder,
  Calendar,
  HardDrive,
  AlertCircle,
  FileCheck2,
  Download,
  FolderOpen,
  ExternalLink
} from 'lucide-react';
import { API_BASE_URL } from '../../lib/apiConfig';

interface ReportFile {
  filename: string;
  content: string;
  size: number;
  updated_at: string;
}

export const ArtifactsView: React.FC = () => {
  const [files, setFiles] = useState<ReportFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<ReportFile | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [editFilename, setEditFilename] = useState('');
  const [isCreatingNew, setIsCreatingNew] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');

  const findMatchingFile = (filename: string, fileList: ReportFile[]) => {
    // 1. Khớp chính xác
    let found = fileList.find(f => f.filename === filename);
    if (found) return found;

    // 2. Khớp không đuôi (thêm hoặc bớt .md)
    const cleanTarget = filename.replace(/\.md$/, '').toLowerCase();
    found = fileList.find(f => f.filename.replace(/\.md$/, '').toLowerCase() === cleanTarget);
    if (found) return found;

    // 3. Khớp một phần tên
    found = fileList.find(f => f.filename.toLowerCase().includes(cleanTarget) || cleanTarget.includes(f.filename.replace(/\.md$/, '').toLowerCase()));
    return found || null;
  };

  const handleSelectFile = (file: ReportFile) => {
    setSelectedFile(file);
    setEditContent(file.content);
    setEditFilename(file.filename);
    setIsEditing(false);
    setIsCreatingNew(false);
    setSaveStatus('idle');
  };

  const fetchFiles = async (selectLatest = false) => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reports`);
      if (!res.ok) throw new Error('Không thể tải danh sách tài liệu');
      const data: ReportFile[] = await res.json();
      setFiles(data);
      if (data.length > 0) {
        // Kiểm tra xem có file yêu cầu mở từ chat không
        const targetFilename = localStorage.getItem('selected_artifact_filename');
        if (targetFilename) {
          const fileToSelect = findMatchingFile(targetFilename, data);
          if (fileToSelect) {
            handleSelectFile(fileToSelect);
            localStorage.removeItem('selected_artifact_filename');
            return;
          }
        }

        if (selectLatest) {
          // Select the first one (most recently updated)
          handleSelectFile(data[0]);
        } else if (!selectedFile || !data.some(f => f.filename === selectedFile.filename)) {
          handleSelectFile(data[0]);
        } else {
          // Refresh selected file data
          const updatedSelected = data.find(f => f.filename === selectedFile.filename);
          if (updatedSelected) {
            setSelectedFile(updatedSelected);
          }
        }
      } else {
        setSelectedFile(null);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Lỗi kết nối server');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    queueMicrotask(() => void fetchFiles());
    // Initial load only; later refreshes are explicit events.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handleSelectFromStorage = () => {
      const targetFilename = localStorage.getItem('selected_artifact_filename');
      if (targetFilename && files.length > 0) {
        const fileToSelect = findMatchingFile(targetFilename, files);
        if (fileToSelect) {
          handleSelectFile(fileToSelect);
          localStorage.removeItem('selected_artifact_filename');
        }
      }
    };
    window.addEventListener('navigate-to-artifacts', handleSelectFromStorage);
    return () => window.removeEventListener('navigate-to-artifacts', handleSelectFromStorage);
  }, [files]);

  const handleCreateNew = () => {
    setIsCreatingNew(true);
    setSelectedFile(null);
    setEditFilename('bao_cao_moi.md');
    setEditContent('# Tài liệu mới\n\nNội dung tài liệu...');
    setIsEditing(true);
    setSaveStatus('idle');
  };

  const handleSave = async () => {
    if (!editFilename.trim()) {
      alert('Vui lòng nhập tên file');
      return;
    }
    setSaveStatus('saving');
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reports`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: editFilename,
          content: editContent
        })
      });
      if (!res.ok) throw new Error('Không thể lưu tài liệu');
      const saved: ReportFile = await res.json();
      
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus('idle'), 2000);
      
      // Refresh list and select the saved file
      await fetchFiles();
      setSelectedFile(saved);
      setIsCreatingNew(false);
      setIsEditing(false);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Lỗi khi lưu file');
      setSaveStatus('idle');
    }
  };

  const handleOpenFolder = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reports/open-folder`, {
        method: 'POST',
      });
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Không thể mở thư mục');
      }
    } catch (err: unknown) {
      console.error('Lỗi khi mở thư mục:', err);
      alert(err instanceof Error ? err.message : 'Lỗi khi mở thư mục');
    }
  };

  const handleDelete = async (filename: string, e: React.MouseEvent) => {
    e.stopPropagation(); // prevent selecting the deleted file
    if (!confirm(`Bạn có chắc chắn muốn xóa file ${filename} không?`)) return;

    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reports/${encodeURIComponent(filename)}`, {
        method: 'DELETE'
      });
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Không thể xóa tài liệu');
      }
      
      // If we deleted the currently selected file, reset selection
      if (selectedFile?.filename === filename) {
        setSelectedFile(null);
      }
      
      // Refresh file list
      await fetchFiles();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Lỗi khi xóa file');
    }
  };

  const filteredFiles = files.filter(f => {
    const query = searchQuery.toLowerCase();
    return f.filename.toLowerCase().includes(query) || f.content.toLowerCase().includes(query);
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
        minute: '2-digit'
      });
    } catch {
      return isoStr;
    }
  };



  const handleDownloadPDF = async () => {
    if (!selectedFile) return;
    const isDocx = selectedFile.filename.endsWith('.docx');

    if (isDocx) {
      // Tải trực tiếp bằng link của backend để trình duyệt tự xử lý stream nhị phân chuẩn xác
      const url = `${API_BASE_URL}/api/v1/reports/${encodeURIComponent(selectedFile.filename)}/download`;
      const link = document.createElement('a');
      link.href = url;
      link.download = selectedFile.filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      return;
    }

    try {
      const url = `${API_BASE_URL}/api/v1/reports/${encodeURIComponent(selectedFile.filename)}/pdf`;
      const res = await fetch(url);
      if (!res.ok) throw new Error('Lỗi từ server khi tạo PDF');

      const blob = await res.blob();
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = selectedFile.filename.replace(/\.md$/, '.pdf');
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(downloadUrl);
    } catch (err: unknown) {
      console.error('Lỗi khi xuất file PDF:', err);
      alert(
        'Không thể tải file PDF từ server: ' +
        (err instanceof Error ? err.message : String(err))
      );
    }
  };

  return (
    <div className="flex-1 flex h-full bg-[#1b1a17] text-[#f3f2ef] overflow-hidden">
      {/* SIDEBAR FILE LIST (BÊN TRÁI) */}
      <aside className="w-[320px] border-r border-[#2d2b27] flex flex-col bg-[#1c1b18] shrink-0">
        <div className="p-4 border-b border-[#2d2b27] flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Folder className="w-5 h-5 text-[#d97757]" />
              <h2 className="text-sm font-semibold tracking-tight">Tài liệu Artifacts</h2>
            </div>
            <button
              onClick={handleCreateNew}
              title="Tạo file mới"
              className="p-1.5 rounded-lg bg-[#2c2a26] text-zinc-300 hover:text-white hover:bg-[#d97757] hover:text-[#1b1a17] transition-all duration-200 cursor-pointer"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
          
          <div className="relative">
            <Search className="absolute left-3 top-2.5 w-4 h-4 text-zinc-500" />
            <input
              type="text"
              placeholder="Tìm kiếm file hoặc nội dung..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-xs rounded-xl bg-[#252320] border border-[#33312d] text-[#f3f2ef] placeholder-zinc-500 focus:outline-none focus:border-[#d97757]/50"
            />
          </div>
        </div>

        {/* FILE LIST */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1 custom-scrollbar">
          {isLoading && files.length === 0 ? (
            <div className="text-center py-8 text-xs text-zinc-500">Đang tải danh sách tài liệu...</div>
          ) : error ? (
            <div className="p-3 text-center text-xs text-rose-400 bg-rose-950/20 rounded-xl border border-rose-900/30 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : filteredFiles.length === 0 ? (
            <div className="text-center py-8 text-xs text-zinc-500">
              {searchQuery ? 'Không tìm thấy file phù hợp' : 'Chưa có tài liệu nào'}
            </div>
          ) : (
            filteredFiles.map((file) => {
              const isSelected = selectedFile?.filename === file.filename && !isCreatingNew;
              return (
                <div
                  key={file.filename}
                  onClick={() => handleSelectFile(file)}
                  className={`group flex items-center justify-between p-3 rounded-xl cursor-pointer transition-all duration-150 ${
                    isSelected
                      ? 'bg-[#d97757]/10 border border-[#d97757]/30 text-white font-medium'
                      : 'border border-transparent hover:bg-[#252320] text-zinc-300 hover:text-white'
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <FileText className={`w-4 h-4 shrink-0 ${isSelected ? 'text-[#d97757]' : 'text-zinc-400'}`} />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs truncate">{file.filename}</p>
                      <div className="flex items-center gap-2 mt-1 text-[10px] text-zinc-500">
                        <span>{formatSize(file.size)}</span>
                        <span>•</span>
                        <span className="truncate">{formatDate(file.updated_at)}</span>
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={(e) => handleDelete(file.filename, e)}
                    className="p-1 rounded opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-rose-400 hover:bg-[#2e2b27] transition-all cursor-pointer"
                    title="Xóa file"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })
          )}
        </div>
        
        {/* FOOTER STATS */}
        <div className="p-3 border-t border-[#2d2b27] bg-[#171614] text-[10px] text-zinc-500 flex items-center justify-between">
          <button
            type="button"
            onClick={handleOpenFolder}
            title="Mở thư mục workspace/reports trong máy tính"
            className="flex items-center gap-1.5 text-zinc-400 hover:text-[#d97757] transition-colors cursor-pointer group"
          >
            <FolderOpen className="w-3.5 h-3.5 text-zinc-400 group-hover:text-[#d97757]" />
            <span className="group-hover:underline">Thư mục: workspace/reports</span>
            <ExternalLink className="w-3 h-3 opacity-60 group-hover:opacity-100" />
          </button>
          <span>{files.length} file</span>
        </div>
      </aside>

      {/* CONTENT PREVIEW & EDITOR (BÊN PHẢI) */}
      <main className="flex-1 flex flex-col bg-[#1b1a17] overflow-hidden">
        {selectedFile || isCreatingNew ? (
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* TOOLBAR */}
            <div className="px-6 py-4 border-b border-[#2d2b27] bg-[#1c1b18] flex items-center justify-between shrink-0">
              <div className="min-w-0 flex-1">
                {isEditing ? (
                  <input
                    type="text"
                    value={editFilename}
                    onChange={(e) => setEditFilename(e.target.value)}
                    className="bg-[#252320] border border-[#33312d] text-sm font-semibold rounded-lg px-3 py-1.5 text-[#f3f2ef] w-full max-w-sm focus:outline-none focus:border-[#d97757]/50 font-mono"
                    placeholder="Ten_file.md"
                  />
                ) : (
                  <div>
                    <h1 className="text-sm font-semibold text-white tracking-tight flex items-center gap-2 truncate font-mono">
                      <FileCode className="w-4 h-4 text-[#d97757]" />
                      {selectedFile?.filename}
                    </h1>
                    <p className="text-[10px] text-zinc-500 mt-1 flex items-center gap-3">
                      <span className="flex items-center gap-1">
                        <HardDrive className="w-3 h-3" /> Dung lượng: {formatSize(selectedFile?.size || 0)}
                      </span>
                      <span className="flex items-center gap-1">
                        <Calendar className="w-3 h-3" /> Cập nhật: {selectedFile ? formatDate(selectedFile.updated_at) : ''}
                      </span>
                    </p>
                  </div>
                )}
              </div>

              <div className="flex items-center gap-2 shrink-0 ml-4">
                {selectedFile && !isEditing && !isCreatingNew && (
                  <button
                    onClick={handleDownloadPDF}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600/10 hover:bg-emerald-600/20 text-emerald-500 rounded-lg text-xs font-semibold transition-colors cursor-pointer"
                  >
                    <Download className="w-3.5 h-3.5" />
                    <span>{selectedFile.filename.endsWith('.docx') ? 'Tải DOCX' : 'Tải PDF'}</span>
                  </button>
                )}

                {!isCreatingNew && (
                  <button
                    onClick={() => setIsEditing(!isEditing)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
                      isEditing
                        ? 'bg-[#2c2a26] text-zinc-300 hover:text-white'
                        : 'bg-[#d97757]/10 hover:bg-[#d97757]/20 text-[#d97757]'
                    }`}
                  >
                    {isEditing ? (
                      <>
                        <Eye className="w-3.5 h-3.5" />
                        <span>Xem Preview</span>
                      </>
                    ) : (
                      <>
                        <Edit2 className="w-3.5 h-3.5" />
                        <span>Chỉnh sửa</span>
                      </>
                    )}
                  </button>
                )}

                {(isEditing || isCreatingNew) && (
                  <button
                    onClick={handleSave}
                    disabled={saveStatus === 'saving'}
                    className="flex items-center gap-1.5 px-4 py-1.5 bg-[#d97757] hover:bg-[#e08862] text-[#1c1b18] rounded-lg text-xs font-semibold transition-colors disabled:opacity-50 cursor-pointer"
                  >
                    <Save className="w-3.5 h-3.5" />
                    <span>{saveStatus === 'saving' ? 'Đang lưu...' : 'Lưu file'}</span>
                  </button>
                )}
              </div>
            </div>

            {/* EDITOR OR PREVIEW */}
            <div className="flex-1 overflow-auto p-6">
              {isEditing ? (
                <div className="h-full flex flex-col gap-2">
                  <span className="text-[10px] text-zinc-500 font-mono">NỘI DUNG TÀI LIỆU (MARKDOWN HỖ TRỢ)</span>
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="flex-1 w-full bg-[#1e1d1a] border border-[#2d2b27] rounded-xl p-4 text-xs font-mono leading-6 text-zinc-200 focus:outline-none focus:border-[#d97757]/40 resize-none h-[calc(100vh-260px)]"
                    placeholder="# Tiêu đề tài liệu..."
                  />
                </div>
              ) : (
                <div className="prose prose-invert max-w-none text-xs leading-6 text-zinc-300">
                  {/* Clean custom markdown parser wrapper */}
                  <div className="bg-[#1c1b18] border border-[#2d2b27] rounded-2xl p-6 shadow-sm overflow-auto whitespace-pre-wrap font-sans">
                    {selectedFile?.content.trim() ? (
                      <MarkdownRenderer text={selectedFile.content} />
                    ) : (
                      <em className="text-zinc-500">Tài liệu trống</em>
                    )}
                  </div>
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
            <h3 className="text-sm font-semibold text-white tracking-tight">Chưa chọn tài liệu</h3>
            <p className="text-xs text-zinc-500 max-w-sm mt-2 leading-5">
              Chọn tài liệu trong danh sách bên trái hoặc gửi yêu cầu tạo tài liệu mới trong phần Chat để AI tự động tạo và lưu trữ.
            </p>
            <button
              onClick={handleCreateNew}
              className="mt-5 flex items-center gap-1.5 px-4 py-2 bg-[#2c2a26] hover:bg-[#d97757] hover:text-[#1b1a17] text-zinc-300 rounded-lg text-xs font-semibold transition-all duration-200 cursor-pointer border border-[#3e3b36]"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Tạo tài liệu mới</span>
            </button>
          </div>
        )}
      </main>
    </div>
  );
};

// Inline markdown parser helper to render bold, italic, and code blocks
const parseInlineMarkdown = (text: string): React.ReactNode[] => {
  const regex = /(\*\*.*?\*\*|\*.*?\*|`.*?`)/g;
  const parts = text.split(regex);
  return parts.map((part, idx) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={idx} className="text-white font-bold">{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('*') && part.endsWith('*')) {
      return <em key={idx} className="italic text-zinc-300">{part.slice(1, -1)}</em>;
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={idx} className="bg-[#131210] text-amber-500 px-1 py-0.5 rounded font-mono text-[11px]">{part.slice(1, -1)}</code>;
    }
    return part;
  });
};

// Extremely simple Markdown Renderer to show beautiful preview without extra packages
/* eslint-disable react-hooks/immutability -- parser state is local to this pure render pass */
const MarkdownRenderer: React.FC<{ text: string }> = ({ text }) => {
  const lines = text.split('\n');
  let inCodeBlock = false;
  let codeContent: string[] = [];

  return (
    <div className="space-y-4 text-zinc-300">
      {lines.map((line, idx) => {
        // Code blocks handler
        if (line.trim().startsWith('```')) {
          if (inCodeBlock) {
            inCodeBlock = false;
            const content = codeContent.join('\n');
            codeContent = [];
            return (
              <pre key={idx} className="bg-[#131210] border border-[#2d2b27] rounded-xl p-4 overflow-x-auto text-[11px] font-mono text-amber-100/90 my-3">
                <code className="block">{content}</code>
              </pre>
            );
          } else {
            inCodeBlock = true;
            return null;
          }
        }

        if (inCodeBlock) {
          codeContent.push(line);
          return null;
        }

        // Headers
        if (line.startsWith('# ')) {
          return <h1 key={idx} className="text-lg font-bold text-white border-b border-[#2d2b27] pb-2 mt-6 mb-3">{line.substring(2)}</h1>;
        }
        if (line.startsWith('## ')) {
          return <h2 key={idx} className="text-base font-semibold text-white mt-5 mb-2">{line.substring(3)}</h2>;
        }
        if (line.startsWith('### ')) {
          return <h3 key={idx} className="text-sm font-semibold text-white mt-4 mb-2">{line.substring(4)}</h3>;
        }

        // Bullet lists
        if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
          return (
            <ul key={idx} className="list-disc pl-5 space-y-1 my-1">
              <li className="text-xs">{parseInlineMarkdown(line.trim().substring(2))}</li>
            </ul>
          );
        }

        // Numbered lists
        const numMatch = line.trim().match(/^(\d+)\.\s(.*)/);
        if (numMatch) {
          return (
            <ol key={idx} className="list-decimal pl-5 space-y-1 my-1">
              <li className="text-xs">{parseInlineMarkdown(numMatch[2])}</li>
            </ol>
          );
        }

        // Horizontal rules
        if (line.trim() === '---' || line.trim() === '***') {
          return <hr key={idx} className="border-[#2d2b27] my-6" />;
        }

        // Blockquotes
        if (line.trim().startsWith('> ')) {
          return (
            <blockquote key={idx} className="border-l-4 border-[#d97757] bg-[#252320] px-4 py-2 rounded-r-lg my-3 italic text-zinc-400">
              {parseInlineMarkdown(line.trim().substring(2))}
            </blockquote>
          );
        }

        // Empty lines
        if (!line.trim()) return <div key={idx} className="h-2" />;

        // Normal paragraph with bold/italic parsing support
        return <p key={idx} className="text-xs leading-6 text-zinc-300">{parseInlineMarkdown(line)}</p>;
      })}
    </div>
  );
};
/* eslint-enable react-hooks/immutability */
