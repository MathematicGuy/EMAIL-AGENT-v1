import React, { useRef, useEffect, useState } from 'react';
import {
  Plus,
  Mic,
  ChevronDown,
  ArrowUp,
  FolderKanban,
  Folder,
  Check,
  Hand,
  Zap,
  FileText,
  LoaderCircle,
  X
} from 'lucide-react';
import type {
  ChatComposerAttachment,
  ModelOption,
} from '../types';
import type { Project } from '../types/projectTypes';

interface ChatInputBoxProps {
  inputText: string;
  onChangeText: (text: string) => void;
  onSend: (text?: string) => void;
  isGenerating?: boolean;
  selectedModel: ModelOption;
  onOpenModelModal: (anchor: DOMRect) => void;
  onOpenVoiceModal: () => void;

  attachments?: ChatComposerAttachment[];
  attachmentError?: string | null;
  onSelectFiles?: (files: File[]) => void;
  onRemoveAttachment?: (attachmentId: string) => void;
  activeProject?: Project;
  projects?: Project[];
  onSelectProject?: (projectId: string) => void;
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.ceil(sizeBytes / 1024)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const ChatInputBox: React.FC<ChatInputBoxProps> = ({
  inputText,
  onChangeText,
  onSend,
  isGenerating,
  selectedModel,
  onOpenModelModal,
  onOpenVoiceModal,

  attachments = [],
  attachmentError,
  onSelectFiles,
  onRemoveAttachment,
  activeProject,
  projects,
  onSelectProject,
}) => {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isProjectDropdownOpen, setIsProjectDropdownOpen] = useState(false);

  // Auto resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 220)}px`;
    }
  }, [inputText]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (inputText.trim() && !isGenerating) {
        onSend();
      }
    }
  };

  return (
    <div className="w-full max-w-3xl md:max-w-4xl mx-auto px-4 select-text">
      {/* Main Floating Card matching image.png */}
      <div className="bg-[#242320] border border-[#35332f] hover:border-[#44413c] focus-within:border-[#524f4a] rounded-2xl shadow-2xl transition-all duration-150 overflow-hidden">
        {/* Top Textarea */}
        <div className="p-3.5 pb-2">
          {attachments.length > 0 && (
            <div
              className="mb-3 flex flex-wrap gap-2"
              aria-label="Tài liệu đã đính kèm"
            >
              {attachments.map((attachment) => (
                <div
                  key={attachment.id}
                  className="flex max-w-full items-center gap-2 rounded-xl border border-[#413d37] bg-[#2b2925] px-2.5 py-2 text-xs text-zinc-200"
                >
                  {attachment.status === 'uploading' ? (
                    <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin text-[#d97757]" />
                  ) : (
                    <FileText className="h-3.5 w-3.5 shrink-0 text-[#d97757]" />
                  )}
                  <span className="max-w-48 truncate">{attachment.name}</span>
                  <span className="shrink-0 text-[10px] text-zinc-500">
                    {attachment.status === 'uploading'
                      ? 'Đang tải'
                      : formatFileSize(attachment.sizeBytes)}
                  </span>
                  {onRemoveAttachment && (
                    <button
                      type="button"
                      onClick={() => onRemoveAttachment(attachment.id)}
                      disabled={isGenerating}
                      aria-label={`Bỏ tệp ${attachment.name}`}
                      className="rounded p-0.5 text-zinc-500 transition-colors hover:bg-[#3a3732] hover:text-zinc-100 disabled:opacity-40"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {attachmentError && (
            <p role="alert" className="mb-2 text-xs text-rose-300">
              {attachmentError}
            </p>
          )}

          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => onChangeText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="How can I help you today?"
            rows={2}
            className="w-full bg-transparent text-[#f3f2ef] placeholder-zinc-500 text-sm focus:outline-none resize-none min-h-[48px] max-h-[220px] leading-relaxed font-sans"
          />

          {/* Middle Control Strip: +, Mode Pill [Chat | Cowork], Model Dropdown, Mic, Send */}
          <div className="flex items-center justify-between pt-1.5 text-xs">
            {/* Left: Plus & Segmented Mode Switcher */}
            <div className="flex items-center gap-2">
              <button
                type="button"
                title="Đính kèm tài liệu"
                aria-label="Đính kèm tài liệu"
                onClick={() => fileInputRef.current?.click()}
                disabled={isGenerating}
                className="w-7 h-7 flex items-center justify-center text-zinc-400 hover:text-zinc-100 hover:bg-[#32302c] rounded-md transition-colors cursor-pointer"
              >
                <Plus className="w-4 h-4" />
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.docx"
                aria-label="Chọn tài liệu từ máy"
                className="sr-only"
                onChange={(event) => {
                  onSelectFiles?.(Array.from(event.target.files ?? []));
                  event.target.value = '';
                }}
              />


            </div>

            {/* Right Controls: Model dropdown, Mic, Send */}
            <div className="flex items-center gap-1.5 text-zinc-400">
              {/* Model Picker (e.g. Fable 5 High ∨) */}
              <button
                onClick={(event) =>
                  onOpenModelModal(event.currentTarget.getBoundingClientRect())
                }
                className="flex items-center gap-1 px-2.5 py-1 bg-transparent hover:bg-[#32302c] rounded-md text-zinc-200 transition-colors font-medium cursor-pointer"
              >
                <span>{selectedModel.name}</span>
                <ChevronDown className="w-3 h-3 text-zinc-500" />
              </button>

              {/* Mic Icon */}
              <button
                onClick={onOpenVoiceModal}
                title="Voice Mode"
                className="w-7 h-7 flex items-center justify-center text-zinc-400 hover:text-zinc-100 hover:bg-[#32302c] rounded-md transition-colors cursor-pointer"
              >
                <Mic className="w-4 h-4" />
              </button>

              {/* Submit / Send button if text exists */}
              {(inputText.trim().length > 0 || attachments.length > 0) && (
                <button
                  onClick={() => onSend()}
                  disabled={isGenerating || inputText.trim().length === 0}
                  title="Send message"
                  className="w-7 h-7 ml-1 flex items-center justify-center bg-zinc-200 hover:bg-white text-zinc-900 rounded-md transition-all shadow-sm cursor-pointer disabled:opacity-50"
                >
                  <ArrowUp className="w-4 h-4 stroke-[2.5]" />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Bottom Sub-Strip matching image.png */}
        <div className="px-3.5 py-2 bg-[#1f1e1c] border-t border-[#312f2b] flex items-center justify-between text-[11px] text-zinc-400">
          {/* Left Context Pills */}
          <div className="flex items-center gap-2">
            <div className="relative">
              <button
                type="button"
                onClick={() => setIsProjectDropdownOpen(!isProjectDropdownOpen)}
                className="flex items-center gap-1.5 px-2.5 py-1 bg-[#282623] hover:bg-[#32302c] rounded-lg text-zinc-300 transition-colors border border-zinc-700/50 cursor-pointer text-xs"
              >
                {activeProject ? (
                  <>
                    <Folder
                      className="w-3 h-3 shrink-0"
                      style={{ color: activeProject.color || '#d97757' }}
                      fill="currentColor"
                    />
                    <span className="text-zinc-200 font-medium max-w-[120px] truncate">
                      {activeProject.name}
                    </span>
                  </>
                ) : (
                  <>
                    <FolderKanban className="w-3 h-3 text-zinc-400" />
                    <span>Project or folder</span>
                  </>
                )}
                <ChevronDown className="w-3 h-3 text-zinc-500 shrink-0" />
              </button>

              {isProjectDropdownOpen && (
                <>
                  <div
                    className="fixed inset-0 z-40"
                    onClick={() => setIsProjectDropdownOpen(false)}
                  />
                  <div className="absolute left-0 bottom-full mb-1.5 w-52 bg-[#22201d] border border-[#383531] rounded-xl shadow-2xl z-50 py-1.5 text-xs select-none">
                    <div className="px-3 py-1 text-[10px] font-semibold tracking-wider text-zinc-500 uppercase">
                      Select Project Context
                    </div>
                    {projects?.map((project) => (
                      <button
                        key={project.id}
                        type="button"
                        onClick={() => {
                          onSelectProject?.(project.id);
                          setIsProjectDropdownOpen(false);
                        }}
                        className={`w-full flex items-center justify-between px-3 py-1.5 hover:bg-[#2c2a26] text-left transition-colors cursor-pointer ${
                          activeProject?.id === project.id ? 'text-white font-semibold bg-[#2a2825]' : 'text-zinc-300'
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <Folder
                            className="w-3.5 h-3.5 shrink-0"
                            style={{ color: project.color || '#d97757' }}
                            fill="currentColor"
                          />
                          <span className="truncate">
                            {project.icon && project.icon !== '📁' ? `${project.icon} ` : ''}
                            {project.name}
                          </span>
                        </div>
                        {activeProject?.id === project.id && (
                          <Check className="w-3.5 h-3.5 text-[#d97757] shrink-0" />
                        )}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            <button className="flex items-center gap-1.5 px-2.5 py-1 bg-[#282623] hover:bg-[#32302c] rounded-lg text-zinc-300 transition-colors border border-zinc-700/50 cursor-pointer">
              <Hand className="w-3 h-3 text-zinc-400" />
              <span>Manual</span>
              <ChevronDown className="w-3 h-3 text-zinc-500" />
            </button>
          </div>

          {/* Right Usage Badge matching image.png */}
          <div className="flex items-center gap-1 text-zinc-400">
            <Zap className="w-3 h-3 text-amber-400" />
            <span>2× more usage until August 5</span>
          </div>
        </div>
      </div>
    </div>
  );
};
