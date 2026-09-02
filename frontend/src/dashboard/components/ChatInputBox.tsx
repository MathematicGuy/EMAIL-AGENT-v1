import React, { useRef, useEffect } from 'react';
import {
  Plus,
  Mic,
  ChevronDown,
  ArrowUp,
  FileText,
  LoaderCircle,
  X,
  Mail,
  Clipboard,
} from 'lucide-react';
import type {
  ChatComposerAttachment,
  ModelOption,
} from '../types';
import type { Project } from '../types/projectTypes';

export interface PromptSuggestion {
  id: string;
  icon: string;
  title: string;
  prompt: string;
}

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
  promptSuggestions?: PromptSuggestion[];
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.ceil(sizeBytes / 1024)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function activeMentionQuery(value: string): string | null {
  const match = value.match(/(?:^|\s)@([a-z]*)$/i);
  return match ? match[1].toLowerCase() : null;
}

const MAIL_MENTIONS = [
  { command: 'mail', label: 'Mail', description: 'Quét Gmail và Outlook đang chọn' },
  { command: 'email', label: 'Email', description: 'Quét Gmail đang chọn' },
  { command: 'outlook', label: 'Outlook', description: 'Quét Outlook đang chọn' },
] as const;

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
  promptSuggestions = [],
}) => {
  void activeProject;
  void projects;
  void onSelectProject;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const hasPendingAttachment = attachments.some((item) => item.status !== 'ready');
  const mentionQuery = activeMentionQuery(inputText);
  const matchingMailMentions = mentionQuery === null
    ? []
    : MAIL_MENTIONS.filter(({ command }) => command.startsWith(mentionQuery));
  const showMailMention = matchingMailMentions.length > 0;

  // Auto resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.max(24, Math.min(textareaRef.current.scrollHeight, 180))}px`;
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

  const selectMailMention = (command: string) => {
    const mentionStart = inputText.lastIndexOf('@');
    onChangeText(`${inputText.slice(0, mentionStart)}@${command} `);
    textareaRef.current?.focus();
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const clipboardFiles = Array.from(e.clipboardData.files || []);
    if (clipboardFiles.length > 0 && onSelectFiles) {
      const validFiles = clipboardFiles.filter(
        (file) =>
          file.name.toLowerCase().endsWith('.pdf') ||
          file.name.toLowerCase().endsWith('.docx') ||
          file.type === 'application/pdf' ||
          file.type === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
      );
      if (validFiles.length > 0) {
        e.preventDefault();
        onSelectFiles(validFiles);
        return;
      }
    }

    if (e.target !== textareaRef.current) {
      const pastedText = e.clipboardData.getData('text');
      if (pastedText) {
        e.preventDefault();
        const nextText = inputText ? `${inputText}\n${pastedText}` : pastedText;
        onChangeText(nextText);
        textareaRef.current?.focus();
      }
    }
  };

  const handleClipboardPasteClick = async () => {
    try {
      if (navigator.clipboard && navigator.clipboard.readText) {
        const text = await navigator.clipboard.readText();
        if (text) {
          const textarea = textareaRef.current;
          if (textarea) {
            const start = textarea.selectionStart ?? inputText.length;
            const end = textarea.selectionEnd ?? inputText.length;
            const updated = inputText.substring(0, start) + text + inputText.substring(end);
            onChangeText(updated);
            setTimeout(() => {
              textarea.focus();
              textarea.setSelectionRange(start + text.length, start + text.length);
            }, 0);
          } else {
            onChangeText(inputText ? `${inputText}\n${text}` : text);
          }
        }
      }
    } catch {
      textareaRef.current?.focus();
    }
  };

  const handleCardClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (
      !target.closest('button') &&
      !target.closest('input') &&
      !target.closest('[role="listbox"]') &&
      target !== textareaRef.current
    ) {
      textareaRef.current?.focus();
    }
  };

  return (
    <div className="w-full max-w-2xl md:max-w-3xl mx-auto px-4 select-text">
      {/* Prompt Suggestions (right above the chat box when empty) */}
      {inputText.trim() === '' && promptSuggestions && promptSuggestions.length > 0 && (
        <div className="mb-2.5 flex flex-wrap items-center justify-center sm:justify-start gap-2">
          {promptSuggestions.map((idea) => (
            <button
              key={idea.id}
              type="button"
              onClick={() =>
                idea.id === 'idea-3'
                  ? onSend(idea.prompt)
                  : onChangeText(idea.prompt)
              }
              className="inline-flex items-center gap-1.5 rounded-full border border-[#383531] bg-[#24221f]/90 hover:border-[#4d4a43] hover:bg-[#2d2b27] px-3 py-1.5 text-xs font-medium text-zinc-300 hover:text-white transition-all duration-150 cursor-pointer shadow-xs"
            >
              <span className="text-xs shrink-0">{idea.icon}</span>
              <span>{idea.title}</span>
            </button>
          ))}
        </div>
      )}

      {/* Main Floating Card matching image.png */}
      <div
        onClick={handleCardClick}
        onPaste={handlePaste}
        className={`bg-[#242320] border border-[#35332f] hover:border-[#44413c] focus-within:border-[#524f4a] rounded-2xl shadow-2xl transition-all duration-150 cursor-text ${
          showMailMention ? 'overflow-visible' : 'overflow-hidden'
        }`}
      >
        {/* Top Textarea */}
        <div className="p-2.5 sm:p-3 pb-2">
          {attachments.length > 0 && (
            <div
              className="mb-3 flex flex-wrap gap-2"
              aria-label="Tài liệu đã đính kèm"
            >
              {attachments.map((attachment) => (
                <div
                  key={attachment.id}
                  data-testid="chat-attachment"
                  data-attachment-status={attachment.status}
                  data-document-id={attachment.documentId ?? ''}
                  className="flex max-w-full items-center gap-2 rounded-xl border border-[#413d37] bg-[#2b2925] px-2.5 py-2 text-xs text-zinc-200"
                >
                  {['hashing', 'uploading', 'processing', 'deleting'].includes(attachment.status) ? (
                    <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin text-[#d97757]" />
                  ) : (
                    <FileText className="h-3.5 w-3.5 shrink-0 text-[#d97757]" />
                  )}
                  <span className="max-w-48 truncate">{attachment.name}</span>
                  <span className="shrink-0 text-[10px] text-zinc-500">
                    {attachment.status === 'hashing'
                      ? 'Đang kiểm tra'
                      : attachment.status === 'uploading'
                        ? 'Đang tải lên'
                        : attachment.status === 'processing'
                          ? 'Đang xử lý'
                          : attachment.status === 'error'
                            ? 'Lỗi'
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

          <div className="relative">
            {showMailMention && (
              <div
                role="listbox"
                aria-label="Gợi ý công cụ"
                className="absolute bottom-full left-0 z-50 mb-3 w-full max-w-md overflow-hidden rounded-xl border border-[#45413b] bg-[#292724] py-1.5 shadow-2xl"
              >
                {matchingMailMentions.map((mention) => (
                  <button
                    key={mention.command}
                    type="button"
                    role="option"
                    aria-selected="false"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => selectMailMention(mention.command)}
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-[#37342f]"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[#5b493d] bg-[#392c24] text-[#e8a78f]">
                      <Mail className="h-4 w-4" />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-zinc-100">{mention.label}</span>
                      <span className="block truncate text-xs text-zinc-400">
                        {mention.description}
                      </span>
                    </span>
                    <span className="ml-auto rounded border border-[#4b4741] px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                      @{mention.command}
                    </span>
                  </button>
                ))}
              </div>
            )}
            <textarea
              ref={textareaRef}
              value={inputText}
              onChange={(e) => onChangeText(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder="Tôi có thể giúp gì cho bạn hôm nay?"
              rows={1}
              className="w-full bg-transparent text-[#f3f2ef] placeholder-zinc-500 text-sm focus:outline-none resize-none min-h-[24px] max-h-[180px] leading-normal font-sans select-text py-0.5"
            />
          </div>

          {/* Middle Control Strip: +, Mode Pill [Chat | Cowork], Model Dropdown, Mic, Send */}
          <div className="flex items-center justify-between pt-1 text-xs">
            {/* Left: Plus & Segmented Mode Switcher */}
            <div className="flex items-center gap-2">
              {onSelectFiles && (
                <>
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
                      onSelectFiles(Array.from(event.target.files ?? []));
                      event.target.value = '';
                    }}
                  />
                </>
              )}

              <button
                type="button"
                title="Dán từ Clipboard (Ctrl+V)"
                aria-label="Dán từ Clipboard"
                onClick={handleClipboardPasteClick}
                disabled={isGenerating}
                className="w-7 h-7 flex items-center justify-center text-zinc-400 hover:text-zinc-100 hover:bg-[#32302c] rounded-md transition-colors cursor-pointer"
              >
                <Clipboard className="w-3.5 h-3.5" />
              </button>
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
                title="Nhập bằng giọng nói"
                className="w-7 h-7 flex items-center justify-center text-zinc-400 hover:text-zinc-100 hover:bg-[#32302c] rounded-md transition-colors cursor-pointer"
              >
                <Mic className="w-4 h-4" />
              </button>

              {/* Submit / Send button if text exists */}
              {(inputText.trim().length > 0 || attachments.length > 0) && (
                <button
                  onClick={() => onSend()}
                  disabled={isGenerating || inputText.trim().length === 0 || hasPendingAttachment}
                  data-testid="chat-send"
                  title="Gửi tin nhắn"
                  className="w-7 h-7 ml-1 flex items-center justify-center bg-zinc-200 hover:bg-white text-zinc-900 rounded-md transition-all shadow-sm cursor-pointer disabled:opacity-50"
                >
                  <ArrowUp className="w-4 h-4 stroke-[2.5]" />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
