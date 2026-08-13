import React, { useRef, useEffect, useState } from 'react';
import {
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  RotateCcw,
  Share2,
  Square,
  ArrowDown,
  Sparkles,
  FileText,
  LoaderCircle,
  ChevronDown
} from 'lucide-react';
import type {
  GroundingSummary,
  SourceSnapshotRef,
} from '../../modules/work-intake/types';
import { CitationBadge } from '../../modules/documents/components/CitationBadge';
import { API_BASE_URL } from '../../lib/apiConfig';
import {
  LOCAL_ASSISTANT_SCOPE,
} from '../../modules/work-intake/assistantApi';

import { readResourceText } from '../../modules/workspace/resourceApi';
import type {
  ChatComposerAttachment,
  ChatMessage,
  ModelOption,
  TaskWorkflow,
} from '../types';
import type { Project } from '../types/projectTypes';
import { ChatInputBox } from './ChatInputBox';
import { StarburstIcon } from './HeroSection';
import { TaskWorkflowCard } from './TaskWorkflowCard';

interface ChatStreamViewProps {
  messages: ChatMessage[];
  inputText: string;
  onChangeText: (text: string) => void;
  onSend: (text?: string) => void;
  isGenerating: boolean;
  onStopGeneration: () => void;
  selectedModel: ModelOption;
  onOpenModelModal: (anchor: DOMRect) => void;
  onOpenVoiceModal: () => void;

  attachments?: ChatComposerAttachment[];
  attachmentError?: string | null;
  onSelectFiles?: (files: File[]) => void;
  onRemoveAttachment?: (attachmentId: string) => void;
  workflows?: Record<string, TaskWorkflow>;
  onApproveWorkflowPlan?: (taskId: string) => Promise<void> | void;
  onReviseWorkflowPlan?: (taskId: string, feedback: string) => Promise<void> | void;
  onRetryWorkflowStep?: (taskId: string, stepId: string) => void;
  onRetryTurn?: (messageId: string) => void;
  activeProject?: Project;
  projects?: Project[];
  onSelectProject?: (projectId: string) => void;
}

interface ContentPart {
  type: 'text' | 'code';
  content: string;
  language?: string;
  isComplete?: boolean;
}

// Collapsible Reasoning Accordion Component
const ReasoningAccordion: React.FC<{ reasoningText: string }> = ({ reasoningText }) => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const cleanText = reasoningText.replace(/🧠\s*\*?Suy nghĩ AI[^:]*:\*?\s*/i, '').trim();

  return (
    <div className="my-3 rounded-xl border border-amber-500/25 bg-[#25221d]/90 overflow-hidden text-xs shadow-md">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-[#2e2a24] hover:bg-[#38332c] text-amber-200 font-medium transition-colors cursor-pointer select-none"
      >
        <div className="flex items-center gap-2.5">
          <span className="text-base leading-none">🧠</span>
          <span className="font-semibold text-amber-300 text-xs tracking-wide">
            Suy nghĩ AI (Nemotron-3 Reasoning Trajectory)
          </span>
        </div>
        <span className="text-amber-400/90 text-[11px] font-mono bg-[#23201b] px-2 py-0.5 rounded border border-amber-500/20">
          {isOpen ? '▼ Thu gọn' : '▶ Xem chi tiết'}
        </span>
      </button>
      {isOpen && (
        <div className="p-4 border-t border-amber-500/20 text-zinc-300 font-sans leading-relaxed whitespace-pre-wrap bg-[#1a1814]/80 text-xs">
          {cleanText || reasoningText}
        </div>
      )}
    </div>
  );
};

// Tokenizing syntax highlighter for vibrant code coloring (One Dark Pro theme)
function highlightSyntax(code: string): React.ReactNode[] {
  const tokenRegex = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`|\/\/.*$|#.*$|\/\*[\s\S]*?\*\/|\b(?:const|let|var|function|return|if|else|import|export|from|async|await|class|interface|type|def|for|while|try|catch|new|public|private|static|default|case|break|continue|in|of|typeof|instanceof)\b|\b(?:true|false|null|undefined)\b|\b\d+(?:\.\d+)?\b|\b[a-zA-Z_]\w*(?=\s*\())/gm;

  const elements: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenRegex.exec(code)) !== null) {
    const textBefore = code.slice(lastIndex, match.index);
    if (textBefore) {
      elements.push(<span key={`txt-${lastIndex}`}>{textBefore}</span>);
    }

    const token = match[0];

    if (token.startsWith('//') || token.startsWith('#') || token.startsWith('/*')) {
      elements.push(
        <span key={`tok-${match.index}`} className="text-zinc-500 italic font-mono">
          {token}
        </span>
      );
    } else if (token.startsWith('"') || token.startsWith("'") || token.startsWith('`')) {
      elements.push(
        <span key={`tok-${match.index}`} className="text-emerald-300 font-medium">
          {token}
        </span>
      );
    } else if (/^(?:const|let|var|function|return|if|else|import|export|from|async|await|class|interface|type|def|for|while|try|catch|new|public|private|static|default|case|break|continue|in|of|typeof|instanceof)$/.test(token)) {
      elements.push(
        <span key={`tok-${match.index}`} className="text-purple-400 font-semibold">
          {token}
        </span>
      );
    } else if (/^(?:true|false|null|undefined)$/.test(token) || /^\d+(?:\.\d+)?$/.test(token)) {
      elements.push(
        <span key={`tok-${match.index}`} className="text-[#d97757] font-semibold">
          {token}
        </span>
      );
    } else {
      elements.push(
        <span key={`tok-${match.index}`} className="text-sky-300 font-medium">
          {token}
        </span>
      );
    }

    lastIndex = match.index + token.length;
  }

  const textAfter = code.slice(lastIndex);
  if (textAfter) {
    elements.push(<span key={`txt-${lastIndex}`}>{textAfter}</span>);
  }

  return elements;
}

// Parses raw markdown into sequential text and code block parts
function parseMarkdownContent(rawText: string): ContentPart[] {
  const parts: ContentPart[] = [];
  const regex = /```(\w*)\n?([\s\S]*?)(?:```|$)/g;

  let lastIndex = 0;
  let match;

  while ((match = regex.exec(rawText)) !== null) {
    if (match.index > lastIndex) {
      parts.push({
        type: 'text',
        content: rawText.slice(lastIndex, match.index)
      });
    }

    const language = match[1] || 'code';
    const codeContent = match[2];
    const isComplete = match[0].endsWith('```');

    parts.push({
      type: 'code',
      language,
      content: codeContent,
      isComplete
    });

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < rawText.length) {
    parts.push({
      type: 'text',
      content: rawText.slice(lastIndex)
    });
  }

  return parts;
}

// Helper component to format inline markdown with rich headers, lists, quotes, tables, and links
function FormattedMarkdownText({
  content,
  sourceIdPrefix = 'artifact',
}: {
  content: string;
  sourceIdPrefix?: string;
}) {
  const renderInline = (str: string): React.ReactNode[] => {
    const tokens: React.ReactNode[] = [];
    const regex = /(\[.*?\]\(https?:\/\/[^\s)]+\)|\[\d+\]|\[chunk_[a-f0-9]{32}(?:\s*,\s*chunk_[a-f0-9]{32})*\]|\*\*.*?\*\*|`.*?`|\*.*?\*)/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = regex.exec(str)) !== null) {
      const index = match.index;
      const matchText = match[0];

      if (index > lastIndex) {
        tokens.push(str.slice(lastIndex, index));
      }

      if (/^\[\d+\]$/.test(matchText)) {
        const marker = Number(matchText.slice(1, -1));
        tokens.push(
          <CitationBadge
            key={`citation-${index}`}
            index={marker}
            onClick={() => {
              const el = document.getElementById(`${sourceIdPrefix}-source-${marker}`);
              if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }}
          />
        );
      } else if (matchText.startsWith('[chunk_')) {
        tokens.push(
          <CitationBadge
            key={`citation-chunk-${index}`}
            index={1}
          />
        );
      } else if (matchText.startsWith('[') && matchText.includes('](') && matchText.endsWith(')')) {
        const linkMatch = matchText.match(/^\[(.*?)\]\((https?:\/\/[^\s)]+)\)$/);
        if (linkMatch) {
          tokens.push(
            <a
              key={`a-${index}`}
              href={linkMatch[2]}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sky-400 hover:text-sky-300 underline font-medium inline-flex items-center gap-0.5"
            >
              <span>{linkMatch[1]}</span>
              <span className="text-[10px]">↗</span>
            </a>
          );
        } else {
          tokens.push(matchText);
        }
      } else if (matchText.startsWith('**') && matchText.endsWith('**')) {
        tokens.push(
          <strong key={`b-${index}`} className="font-bold text-white tracking-wide">
            {matchText.slice(2, -2)}
          </strong>
        );
      } else if (matchText.startsWith('`') && matchText.endsWith('`')) {
        tokens.push(
          <code
            key={`c-${index}`}
            className="bg-[#282623] text-[#e89b82] px-1.5 py-0.5 rounded-md text-xs font-mono border border-[#3d3a35] shadow-xs"
          >
            {matchText.slice(1, -1)}
          </code>
        );
      } else if (matchText.startsWith('*') && matchText.endsWith('*')) {
        tokens.push(
          <em key={`i-${index}`} className="italic text-zinc-300 font-serif">
            {matchText.slice(1, -1)}
          </em>
        );
      } else {
        tokens.push(matchText);
      }

      lastIndex = index + matchText.length;
    }

    if (lastIndex < str.length) {
      tokens.push(str.slice(lastIndex));
    }

    return tokens;
  };

  const normalizedContent = (content || '').replace(/\\n/g, '\n').replace(/\\t/g, '\t');
  const lines = normalizedContent.split('\n');

  const blocks: Array<{ type: 'line' | 'table'; lines: string[]; lineIdx: number }> = [];

  let currentTableLines: string[] = [];
  let currentTableStart = 0;

  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      if (currentTableLines.length === 0) {
        currentTableStart = idx;
      }
      currentTableLines.push(trimmed);
    } else {
      if (currentTableLines.length > 0) {
        blocks.push({ type: 'table', lines: currentTableLines, lineIdx: currentTableStart });
        currentTableLines = [];
      }
      blocks.push({ type: 'line', lines: [line], lineIdx: idx });
    }
  });
  if (currentTableLines.length > 0) {
    blocks.push({ type: 'table', lines: currentTableLines, lineIdx: currentTableStart });
  }

  return (
    <div className="space-y-2 select-text text-[#f3f2ef]">
      {blocks.map((block) => {
        if (block.type === 'table') {
          const headerLine = block.lines[0];
          const dataLines = block.lines.filter((l) => !l.match(/^\|[\s:-]+\|$/));
          const headers = headerLine
            .split('|')
            .map((s) => s.trim())
            .filter((_, i, arr) => i > 0 && i < arr.length - 1);
          const rows = dataLines.slice(1).map((l) =>
            l
              .split('|')
              .map((s) => s.trim())
              .filter((_, i, arr) => i > 0 && i < arr.length - 1)
          );

          return (
            <div key={`tbl-${block.lineIdx}`} className="my-3 overflow-x-auto rounded-xl border border-[#383531] shadow-md bg-[#1d1b18]">
              <table className="w-full text-xs text-left border-collapse">
                <thead>
                  <tr className="bg-[#2a2824] border-b border-[#383531] text-[#d97757]">
                    {headers.map((h, hIdx) => (
                      <th key={hIdx} className="px-3.5 py-2.5 font-semibold tracking-wide">
                        {renderInline(h)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#2d2b27]">
                  {rows.map((row, rIdx) => (
                    <tr key={rIdx} className="hover:bg-[#252320] transition-colors">
                      {row.map((cell, cIdx) => (
                        <td key={cIdx} className="px-3.5 py-2 text-zinc-300 leading-relaxed">
                          {renderInline(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        const line = block.lines[0];
        const trimmed = line.trim();
        const sourceAnchor = trimmed.match(
          /^<a id="source-(\d+)"><\/a>(.*)$/
        );
        if (sourceAnchor) {
          return (
            <div
              id={`${sourceIdPrefix}-source-${sourceAnchor[1]}`}
              key={block.lineIdx}
              className="scroll-mt-4 rounded-lg border border-emerald-900/60 bg-emerald-950/20 px-3 py-2 text-xs text-zinc-300"
            >
              {sourceAnchor[2]}
            </div>
          );
        }

        if (!trimmed) {
          return <div key={block.lineIdx} className="h-1.5" />;
        }

        // Horizontal Rule (--- or *** or ___)
        if (/^(---|\*\*\*|___)$/.test(trimmed)) {
          return <hr key={block.lineIdx} className="my-3 border-[#383531]" />;
        }

        // Reasoning Accordion line (🧠 *Suy nghĩ AI...)
        if (trimmed.includes('🧠') && (trimmed.includes('Suy nghĩ AI') || trimmed.includes('Nemotron'))) {
          return <ReasoningAccordion key={block.lineIdx} reasoningText={trimmed} />;
        }

        // H1 Title (# )
        if (trimmed.startsWith('# ')) {
          return (
            <h1 key={block.lineIdx} className="text-lg font-bold text-[#f3f2ef] pt-3 pb-1 border-b border-[#35332f] flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-[#d97757]" />
              <span>{renderInline(trimmed.slice(2))}</span>
            </h1>
          );
        }

        // H2 Subtitle (## )
        if (trimmed.startsWith('## ')) {
          return (
            <h2 key={block.lineIdx} className="text-base font-semibold text-[#e89b82] pt-2 pb-0.5">
              {renderInline(trimmed.slice(3))}
            </h2>
          );
        }

        // H3 Heading (### )
        if (trimmed.startsWith('### ')) {
          return (
            <h3 key={block.lineIdx} className="text-sm font-semibold text-amber-200/90 pt-1">
              {renderInline(trimmed.slice(4))}
            </h3>
          );
        }

        // Blockquote (> )
        if (trimmed.startsWith('> ')) {
          return (
            <blockquote key={block.lineIdx} className="border-l-3 border-[#d97757] bg-[#24221f]/90 px-4 py-2 my-1.5 rounded-r-xl text-zinc-300 italic text-xs leading-relaxed shadow-sm">
              {renderInline(trimmed.slice(2))}
            </blockquote>
          );
        }

        // Bullet list item (- or *)
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          return (
            <div key={block.lineIdx} className="flex items-start gap-2.5 py-0.5 text-zinc-200">
              <span className="w-1.5 h-1.5 rounded-full bg-[#d97757] mt-2 shrink-0 shadow-xs" />
              <div className="flex-1 leading-relaxed">{renderInline(trimmed.slice(2))}</div>
            </div>
          );
        }

        // Numbered list item ("1. ", "2. ", etc.)
        const numMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
        if (numMatch) {
          return (
            <div key={block.lineIdx} className="flex items-start gap-2.5 py-0.5 text-zinc-200">
              <span className="text-[11px] font-bold text-[#d97757] px-1.5 py-0.2 bg-[#2a2824] rounded border border-[#3d3a35] shrink-0 mt-0.5">
                {numMatch[1]}
              </span>
              <div className="flex-1 leading-relaxed">{renderInline(numMatch[2])}</div>
            </div>
          );
        }

        return (
          <div key={block.lineIdx} className="leading-relaxed text-sm text-[#f3f2ef]">
            {renderInline(line)}
          </div>
        );
      })}
    </div>
  );
}

function resourceName(ref: SourceSnapshotRef): string {
  const uploadedName = ref.provenance?.upload_filename;
  return typeof uploadedName === 'string'
    ? uploadedName
    : ref.source_id || ref.ref_id;
}

function ArtifactRefCard({
  artifact,
  grounding,
}: {
  artifact: SourceSnapshotRef;
  grounding?: GroundingSummary;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation(); // Tránh kích hoạt click của Card cha để link sang Artifacts
    if (isOpen) {
      setIsOpen(false);
      return;
    }
    setIsOpen(true);
    if (content !== null || isLoading) return;
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/reports/${encodeURIComponent(artifact.ref_id)}`);
      if (res.ok) {
        setContent(await res.text());
      } else {
        let detail = `Không đọc được báo cáo (HTTP ${res.status}).`;
        try {
          const payload = (await res.json()) as { detail?: string };
          if (payload.detail) detail = payload.detail;
        } catch { /* keep status text */ }
        setError(detail);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Lỗi kết nối server.');
    } finally {
      setIsLoading(false);
    }

  };


  const handleLinkToArtifact = () => {
    const name = resourceName(artifact);
    if (name) {
      localStorage.setItem('selected_artifact_filename', name);
      window.dispatchEvent(new CustomEvent('navigate-to-artifacts'));
    }
  };

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-[#413d37] bg-[#24221f]">
      <div
        onClick={handleLinkToArtifact}
        className="flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left transition-colors hover:bg-[#2d2a26] cursor-pointer"
      >
        <span className="flex min-w-0 items-center gap-2.5">
          <FileText className="h-4 w-4 shrink-0 text-[#d97757]" />
          <span className="min-w-0">
            <span className="block truncate text-xs font-semibold text-zinc-100">
              {resourceName(artifact)}
            </span>
            {(() => {
              const name = resourceName(artifact);
              const isFriendly = name && !name.startsWith('slot_') && !name.startsWith('staged_') && !name.startsWith('source_');
              return (!isFriendly || name === artifact.ref_id) && (
                <span className="block truncate font-mono text-[10px] text-zinc-500">
                  {artifact.ref_id}
                </span>
              );
            })()}
            {grounding && (
              <span
                className={`mt-1 inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ${
                  grounding.status === 'GROUNDED'
                    ? 'bg-emerald-950 text-emerald-300'
                    : 'bg-amber-950 text-amber-300'
                }`}
              >
                {grounding.status === 'GROUNDED' ? '🟢 ' : '🟡 '}
                {grounding.label}
              </span>
            )}
          </span>
        </span>
        <button
          type="button"
          onClick={toggle}
          className="flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-[11px] text-[#e29a7e] bg-[#2d2a26] hover:bg-[#3d3a36] border border-[#413d37] cursor-pointer transition-colors"
          title="Mở rộng xem tại chỗ"
        >
          {isLoading ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <>
              <span>{isOpen ? 'Đóng' : 'Mở báo cáo'}</span>
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform ${
                  isOpen ? 'rotate-180' : ''
                }`}
              />
            </>
          )}
        </button>
      </div>
      {isOpen && (
        <div className="max-h-96 overflow-y-auto border-t border-[#38342f] px-4 py-3 text-sm">
          {error ? (
            <p role="alert" className="text-rose-300">
              {error}
            </p>
          ) : content !== null ? (
            <FormattedMarkdownText
              content={content}
              sourceIdPrefix={artifact.ref_id}
            />
          ) : (
            <p className="text-zinc-500">Đang đọc immutable artifact…</p>
          )}
        </div>
      )}
    </div>
  );
}

export const ChatStreamView: React.FC<ChatStreamViewProps> = ({
  messages,
  inputText,
  onChangeText,
  onSend,
  isGenerating,
  onStopGeneration,
  selectedModel,
  onOpenModelModal,
  onOpenVoiceModal,

  attachments,
  attachmentError,
  onSelectFiles,
  onRemoveAttachment,
  workflows,
  onApproveWorkflowPlan,
  onReviseWorkflowPlan,
  onRetryWorkflowStep,
  onRetryTurn,
  activeProject,
  projects,
  onSelectProject,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef<boolean>(false);
  const prevLastMessageRef = useRef<ChatMessage | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState<boolean>(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const isVietnamese = navigator.language.toLowerCase().startsWith('vi');

  // Detect when user scrolls up
  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

    if (distanceFromBottom > 80) {
      userScrolledRef.current = true;
      setShowScrollToBottom(true);
    } else {
      userScrolledRef.current = false;
      setShowScrollToBottom(false);
    }
  };

  const scrollToBottom = () => {
    userScrolledRef.current = false;
    setShowScrollToBottom(false);
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  };

  // Single effect for message-driven scroll behavior and streaming auto-scroll
  useEffect(() => {
    const lastMsg = messages[messages.length - 1];
    const prevLast = prevLastMessageRef.current;

    const isNewUserMessage =
      !!lastMsg &&
      lastMsg.role === 'user' &&
      (!prevLast || prevLast.id !== lastMsg.id);

    if (isNewUserMessage) {
      userScrolledRef.current = false;
    }

    if (!userScrolledRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }

    prevLastMessageRef.current = lastMsg ?? null;
  }, [messages, isGenerating]);

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden relative">
      {/* Scrollable Messages Stream Container */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 w-full overflow-y-auto custom-scrollbar select-text"
      >
        {/* Inner Centered Messages Wrapper (Slightly wider max-w-4xl md:max-w-5xl) */}
        <div className="max-w-4xl md:max-w-5xl w-full mx-auto px-4 py-6 space-y-6">
          {messages.map((msg) => {
            const isUser = msg.role === 'user';
            const parts = parseMarkdownContent(msg.content);

            return (
              <div key={msg.id} className="w-full flex flex-col gap-2">
                {/* Message Header */}
                <div className="flex items-center justify-between text-xs text-zinc-400 select-none px-1">
                  <div className="flex items-center gap-2.5">
                    {isUser ? (
                      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#d97757] to-[#b85c3e] text-white text-xs font-semibold flex items-center justify-center shadow-sm">
                        DM
                      </div>
                    ) : (
                      <div className="w-7 h-7 rounded-xl bg-[#282623] border border-[#3d3a36] shadow-md flex items-center justify-center p-1">
                        <StarburstIcon className="w-full h-full" />
                      </div>
                    )}

                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-zinc-200 text-xs">
                        {isUser ? 'You' : 'F-Cowork AI'}
                      </span>
                      {!isUser && (
                        <span className="text-[10px] font-medium bg-[#2a2825] text-[#d97757] px-2 py-0.5 rounded border border-[#3d3a36]">
                          {selectedModel.name}
                        </span>
                      )}
                    </div>
                  </div>

                  <span className="text-zinc-500 text-[11px] font-normal">{msg.timestamp}</span>
                </div>

                {/* Clean Message Card Container */}
                <div
                  className={`w-full text-sm leading-relaxed font-sans select-text transition-all relative overflow-hidden ${
                    isUser
                      ? 'p-4 rounded-2xl bg-[#24221e] border border-[#383531] text-zinc-100 shadow-sm'
                      : 'p-5 rounded-2xl bg-[#1e1d1a] border border-[#2d2b27] text-[#f3f2ef] shadow-md space-y-3 backdrop-blur-sm'
                  }`}
                >
                  {/* Subtle top ambient glowing gradient line on AI messages */}
                  {!isUser && (
                    <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-[#d97757]/40 to-transparent" />
                  )}

                  {parts.map((part, pIdx) => {
                    const isLastPart = pIdx === parts.length - 1;

                    if (part.type === 'text') {
                      return (
                        <div key={pIdx} className="relative">
                          <FormattedMarkdownText content={part.content} />
                          {msg.isStreaming && isLastPart && (
                            <span className="inline-block w-2 h-4 ml-1 bg-[#d97757] animate-caret align-middle select-none shadow-[0_0_8px_rgba(217,119,87,0.8)]" />
                          )}
                        </div>
                      );
                    } else {
                      // Ultra-Clean Code Block with One Dark Pro Syntax Highlighting
                      return (
                        <div
                          key={pIdx}
                          className="rounded-xl bg-[#141311] border border-[#2e2c28] overflow-hidden my-4 shadow-lg select-text"
                        >
                          {/* Code Header Bar with macOS dots and icon-only copy button */}
                          <div className="flex items-center justify-between px-4 py-2 bg-[#1f1e1b] border-b border-[#2d2b27] text-xs text-zinc-400 select-none">
                            <div className="flex items-center gap-3">
                              <div className="flex items-center gap-1.5">
                                <span className="w-2.5 h-2.5 rounded-full bg-rose-500/70" />
                                <span className="w-2.5 h-2.5 rounded-full bg-amber-500/70" />
                                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/70" />
                              </div>
                              <span className="font-mono text-[11px] font-medium text-zinc-400 uppercase tracking-wider">
                                {part.language || 'CODE'}
                              </span>
                            </div>

                            {/* Icon-Only Copy Button */}
                            <button
                              onClick={() => handleCopy(part.content, `${msg.id}-code-${pIdx}`)}
                              className="p-1 hover:text-zinc-100 hover:bg-[#2c2a26] rounded transition-colors cursor-pointer"
                              title="Copy code"
                            >
                              {copiedId === `${msg.id}-code-${pIdx}` ? (
                                <Check className="w-3.5 h-3.5 text-emerald-400" />
                              ) : (
                                <Copy className="w-3.5 h-3.5" />
                              )}
                            </button>
                          </div>

                          {/* Code Body with Rich Syntax Highlighting */}
                          <pre className="p-4 text-xs font-mono text-zinc-200 overflow-x-auto leading-relaxed whitespace-pre select-text">
                            <code>{highlightSyntax(part.content)}</code>
                            {msg.isStreaming && isLastPart && (
                              <span className="inline-block w-2 h-4 ml-1 bg-emerald-300 animate-caret align-middle select-none" />
                            )}
                          </pre>
                        </div>
                      );
                    }
                  })}

                  {/* Caret fallback if message is streaming and content is empty */}
                  {msg.isStreaming && parts.length === 0 && (
                    <span className="inline-block w-2 h-4 bg-[#d97757] animate-caret align-middle select-none shadow-[0_0_8px_rgba(217,119,87,0.8)]" />
                  )}

                  {msg.attachmentRefs && msg.attachmentRefs.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {msg.attachmentRefs.map((attachment) => (
                        <span
                          key={attachment.ref_id}
                          className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-[#48433d] bg-[#2b2925] px-2.5 py-1.5 text-[11px] text-zinc-300"
                        >
                          <FileText className="h-3.5 w-3.5 shrink-0 text-[#d97757]" />
                          <span className="truncate">{resourceName(attachment)}</span>
                        </span>
                      ))}
                    </div>
                  )}

                  {!isUser && msg.citations && msg.citations.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2" aria-label="Document citations">
                      {msg.citations.map((citation) => (
                        <span
                          key={citation.citationId}
                          title={citation.section}
                          className="inline-flex items-center gap-1.5 rounded-full border border-[#5a5149] bg-[#2b2925] px-3 py-1.5 text-[11px] text-zinc-200"
                        >
                          <FileText className="h-3 w-3 text-[#d97757]" />
                          {citation.unavailable
                            ? `${citation.documentTitle} · ${isVietnamese ? 'không khả dụng' : 'unavailable'}`
                            : `${citation.documentTitle}${citation.section ? ` · ${citation.section}` : ''} · ${isVietnamese ? 'trang' : 'page'} ${citation.pageStart}${citation.pageEnd !== citation.pageStart ? `–${citation.pageEnd}` : ''}`}
                        </span>
                      ))}
                    </div>
                  )}

                  {!isUser && msg.quickActions && msg.quickActions.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {msg.quickActions.map((action) => (
                        <button
                          key={action}
                          type="button"
                          onClick={() => onSend(action)}
                          className="rounded-full border border-[#5a5149] bg-[#2b2925] px-3 py-1.5 text-xs text-zinc-200 transition-colors hover:border-[#d97757] hover:bg-[#34302b]"
                        >
                          {action}
                        </button>
                      ))}
                    </div>
                  )}

                  {!isUser && msg.taskId && workflows?.[msg.taskId] && (
                    <TaskWorkflowCard
                      workflow={workflows[msg.taskId]}
                      onApprove={onApproveWorkflowPlan}
                      onRevise={onReviseWorkflowPlan}
                      onRetry={onRetryWorkflowStep}
                    />
                  )}

                  {!isUser && msg.taskId && !workflows?.[msg.taskId] && (
                    <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-[#38342f] bg-[#24221f] px-3 py-2 text-[11px]">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-amber-300" />
                      <span className="text-zinc-300">Đang chuẩn bị công việc…</span>
                      {msg.taskStatus && (
                        <span className="rounded-full bg-[#34312d] px-2 py-0.5 text-[#e29a7e]">
                          Đang xử lý
                        </span>
                      )}
                    </div>
                  )}

                  {!isUser &&
                    msg.artifactRefs?.map((artifact) => (
                      <ArtifactRefCard
                        key={artifact.ref_id}
                        artifact={artifact}
                        grounding={
                          msg.artifactGrounding?.find(
                            (item) => item.artifact_ref_id === artifact.ref_id
                          )?.grounding
                        }
                      />
                    ))}
                </div>

                {/* Ultra-Clean Icon-Only Action Toolbar for AI responses */}
                {!isUser && !msg.isStreaming && (
                  <div className="flex items-center gap-1 mt-1 text-zinc-500 text-xs select-none px-1">
                    <button
                      onClick={() => handleCopy(msg.content, msg.id)}
                      className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
                      title="Copy response"
                    >
                      {copiedId === msg.id ? (
                        <Check className="w-3.5 h-3.5 text-emerald-400" />
                      ) : (
                        <Copy className="w-3.5 h-3.5" />
                      )}
                    </button>

                    <button
                      className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
                      title="Good response"
                    >
                      <ThumbsUp className="w-3.5 h-3.5" />
                    </button>

                    <button
                      className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
                      title="Bad response"
                    >
                      <ThumbsDown className="w-3.5 h-3.5" />
                    </button>

                    <button
                      onClick={() => {
                        if (onRetryTurn) {
                          onRetryTurn(msg.id);
                        } else {
                          const idx = messages.findIndex((m) => m.id === msg.id);
                          const userMsg = idx > 0 ? messages[idx - 1] : null;
                          if (userMsg && userMsg.role === 'user') {
                            onSend(userMsg.content);
                          }
                        }
                      }}
                      className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
                      title="Retry"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>

                    <button
                      className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
                      title="Share conversation"
                    >
                      <Share2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Floating Scroll to Bottom Button if user scrolled up */}
      {showScrollToBottom && (
        <div className="absolute bottom-24 right-8 z-20 select-none">
          <button
            onClick={scrollToBottom}
            className="p-2 bg-[#2c2a26] hover:bg-[#383632] text-zinc-200 border border-zinc-700/60 rounded-full shadow-lg transition-all cursor-pointer flex items-center justify-center"
            title="Scroll to bottom"
          >
            <ArrowDown className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Stop Generation Button if generating */}
      {isGenerating && (
        <div className="flex justify-center mb-2 z-10 select-none">
          <button
            onClick={onStopGeneration}
            className="flex items-center gap-2 px-3.5 py-1.5 bg-[#2a2926] hover:bg-[#34322e] border border-[#3d3a36] rounded-full text-xs text-zinc-300 transition-colors shadow-md cursor-pointer"
          >
            <Square className="w-3 h-3 fill-current text-rose-400" />
            <span>Stop generating</span>
          </button>
        </div>
      )}

      {/* Fixed Bottom Floating Input Box */}
      <div className="pb-4 pt-2 bg-gradient-to-t from-[#1b1a17] via-[#1b1a17]/90 to-transparent select-none">
        <ChatInputBox
          inputText={inputText}
          onChangeText={onChangeText}
          onSend={onSend}
          isGenerating={isGenerating}
          selectedModel={selectedModel}
          onOpenModelModal={onOpenModelModal}
          onOpenVoiceModal={onOpenVoiceModal}

          attachments={attachments}
          attachmentError={attachmentError}
          onSelectFiles={onSelectFiles}
          onRemoveAttachment={onRemoveAttachment}
          activeProject={activeProject}
          projects={projects}
          onSelectProject={onSelectProject}
        />
      </div>
    </div>
  );
};
