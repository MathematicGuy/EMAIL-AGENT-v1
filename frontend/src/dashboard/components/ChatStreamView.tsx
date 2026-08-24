import React, { useRef, useEffect, useState, useMemo, useCallback } from 'react';
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
  ChevronDown,
  Mail,
  ExternalLink,
} from 'lucide-react';
import type {
  GroundingSummary,
  SourceSnapshotRef,
} from '../../modules/work-intake/types';
import { CitationBadge } from '../../modules/documents/components/CitationBadge';
import {
  LOCAL_ASSISTANT_SCOPE,
} from '../../modules/work-intake/assistantApi';
import { readResourceText } from '../../modules/workspace/resourceApi';
import type {
  ChatComposerAttachment,
  ChatMessage,
  ChatRagEvidence,
  ModelOption,
  TaskWorkflow,
} from '../types';
import type { Project } from '../types/projectTypes';
import { ChatInputBox } from './ChatInputBox';
import { StarburstIcon } from './HeroSection';
import { TaskWorkflowCard } from './TaskWorkflowCard';
import { RagEvidencePanel } from './RagEvidencePanel';
import { AgentActivityTimeline } from './AgentActivityTimeline';

interface ChatStreamViewProps {
  messages: ChatMessage[];
  isTranscriptLoading?: boolean;
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
  onLoadFullEvidence?: (chunkId: string) => Promise<ChatRagEvidence | null>;
  activeProject?: Project;
  projects?: Project[];
  onSelectProject?: (projectId: string) => void;
  onOpenMailInbox?: () => void;
  selectedTraceMessageId?: string | null;
  onOpenExecutionTrace?: (message: ChatMessage) => void;
}

const MailScanCard: React.FC<{
  scan: NonNullable<ChatMessage['mailScan']>;
  onOpenMailInbox?: () => void;
}> = ({ scan, onOpenMailInbox }) => {
  const isFinished = ['succeeded', 'partial', 'failed'].includes(scan.status);
  const denominator = Math.max(scan.emailsToProcess, 1);
  const percentage = Math.min(100, Math.round((scan.emailsProcessed / denominator) * 100));
  const label = {
    connecting: 'Đang kết nối hộp thư',
    queued: 'Đang chờ quét',
    running: 'Đang quét email',
    succeeded: 'Quét hoàn tất',
    partial: 'Quét hoàn tất một phần',
    failed: 'Quét không thành công',
  }[scan.status];
  return (
    <section className="rounded-xl border border-[#4d4035] bg-[#25211d] p-4" aria-label="Tiến độ quét mail">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="flex items-center gap-2 font-medium text-zinc-100">
          <Mail className="h-4 w-4 text-[#e8a78f]" /> {label}
        </span>
        {!isFinished && <LoaderCircle className="h-4 w-4 animate-spin text-[#e8a78f]" />}
      </div>
      <div className="mt-3 flex justify-between text-xs text-zinc-400">
        <span>{scan.emailsProcessed}/{scan.emailsToProcess || scan.emailsProcessed} email</span>
        {scan.actionItemsCount !== undefined && <span>{scan.actionItemsCount} công việc</span>}
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded bg-zinc-800">
        <div className="h-full bg-[#d97757] transition-all duration-300" style={{ width: `${percentage}%` }} />
      </div>
      {isFinished && (
        <a
          href="?view=mail"
          onClick={(event) => {
            if (!onOpenMailInbox) return;
            event.preventDefault();
            onOpenMailInbox();
          }}
          className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-[#e8a78f] hover:text-[#f2b9a4]"
        >
          <ExternalLink className="h-3.5 w-3.5" /> Mở hộp thư
        </a>
      )}
    </section>
  );
};

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
const FormattedMarkdownText = React.memo(function FormattedMarkdownText({
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

  const lines = content.split('\n');
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
});

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

  const filename = resourceName(artifact);
  const title = typeof artifact.provenance?.title === 'string' ? artifact.provenance.title : filename;

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isOpen) {
      setIsOpen(false);
      return;
    }
    setIsOpen(true);
    if (content !== null || isLoading) return;
    setIsLoading(true);
    setError(null);
    try {
      let loadedText: string | null = null;
      try {
        const res = await fetch('/api/v1/reports');
        if (res.ok) {
          const files = (await res.json()) as Array<{ filename: string; content: string }>;
          const match = files.find((f) => f.filename === filename || f.filename === artifact.ref_id);
          if (match && typeof match.content === 'string') {
            loadedText = match.content;
          }
        }
      } catch {
        // Fall back to readResourceText
      }
      if (loadedText === null) {
        loadedText = await readResourceText(
          artifact.ref_id,
          LOCAL_ASSISTANT_SCOPE
        );
      }
      setContent(loadedText);
    } catch (readError) {
      setError(
        readError instanceof Error
          ? readError.message
          : 'Không đọc được nội dung báo cáo.'
      );
    } finally {
      setIsLoading(false);
    }
  };

  const handleLinkToArtifact = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (filename) {
      localStorage.setItem('selected_artifact_filename', filename);
      window.dispatchEvent(new CustomEvent('navigate-to-artifacts'));
    }
  };

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-[#484139] bg-[#24211d] shadow-sm">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#352a22] border border-[#5d3f31]">
            <FileText className="h-5 w-5 text-[#d97757]" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="rounded bg-[#38261e] border border-[#6b4534] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[#e89b82]">
                Artifact Báo cáo
              </span>
              {grounding && (
                <span
                  className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    grounding.status === 'GROUNDED'
                      ? 'bg-emerald-950 text-emerald-300'
                      : 'bg-amber-950 text-amber-300'
                  }`}
                >
                  {grounding.status === 'GROUNDED' ? '🟢 ' : '🟡 '}
                  {typeof grounding.label === 'string' ? grounding.label : ''}
                </span>
              )}
            </div>
            <p className="mt-1 truncate text-xs font-semibold text-zinc-100">
              {title}
            </p>
            {title !== filename && (
              <p className="truncate font-mono text-[11px] text-zinc-400">
                {filename}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0 pt-1 sm:pt-0">
          <button
            type="button"
            onClick={toggle}
            className="flex items-center gap-1 rounded-lg border border-[#413d37] bg-[#1a1917] px-2.5 py-1.5 text-xs text-zinc-300 transition-colors hover:bg-[#2e2a25] hover:text-zinc-100 cursor-pointer"
            title="Xem trước nội dung tại chỗ"
          >
            {isLoading ? (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <>
                <span>{isOpen ? 'Đóng xem trước' : 'Xem trước'}</span>
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform ${
                    isOpen ? 'rotate-180' : ''
                  }`}
                />
              </>
            )}
          </button>

          <button
            type="button"
            onClick={handleLinkToArtifact}
            className="flex items-center gap-1.5 rounded-lg border border-[#6b4231] bg-[#3c251b] px-3 py-1.5 text-xs font-medium text-[#f2a891] transition-colors hover:bg-[#523325] hover:text-white cursor-pointer"
            title="Mở trong trang Artifacts"
          >
            <span>Xem trong Artifacts</span>
            <ExternalLink className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {isOpen && (
        <div className="max-h-96 overflow-y-auto border-t border-[#38342f] bg-[#1a1917] px-4 py-3 text-sm">
          {error ? (
            <p role="alert" className="text-rose-300 text-xs">
              {error}
            </p>
          ) : content !== null ? (
            <FormattedMarkdownText
              content={content}
              sourceIdPrefix={artifact.ref_id}
            />
          ) : (
            <p className="text-xs text-zinc-400">Đang đọc nội dung báo cáo…</p>
          )}
        </div>
      )}
    </div>
  );
}

interface CodeBlockItemProps {
  part: ContentPart;
  pIdx: number;
  msgId: string;
  copiedId: string | null;
  onCopy: (text: string, id: string) => void;
  isStreaming?: boolean;
  isLastPart?: boolean;
}

const CodeBlockItem: React.FC<CodeBlockItemProps> = React.memo(({
  part,
  pIdx,
  msgId,
  copiedId,
  onCopy,
  isStreaming,
  isLastPart,
}) => {
  const highlighted = useMemo(() => highlightSyntax(part.content), [part.content]);

  return (
    <div className="rounded-xl bg-[#141311] border border-[#2e2c28] overflow-hidden my-4 shadow-lg select-text">
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

        <button
          onClick={() => onCopy(part.content, `${msgId}-code-${pIdx}`)}
          className="p-1 hover:text-zinc-100 hover:bg-[#2c2a26] rounded transition-colors cursor-pointer"
          title="Sao chép mã"
        >
          {copiedId === `${msgId}-code-${pIdx}` ? (
            <Check className="w-3.5 h-3.5 text-emerald-400" />
          ) : (
            <Copy className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      <pre className="p-4 text-xs font-mono text-zinc-200 overflow-x-auto leading-relaxed whitespace-pre select-text">
        <code>{highlighted}</code>
        {isStreaming && isLastPart && (
          <span className="inline-block w-2 h-4 ml-1 bg-emerald-300 animate-caret align-middle select-none" />
        )}
      </pre>
    </div>
  );
});

interface ChatMessageItemProps {
  msg: ChatMessage;
  selectedModel: ModelOption;
  copiedId: string | null;
  onCopy: (text: string, id: string) => void;
  onSend: (text?: string) => void;
  workflows?: Record<string, TaskWorkflow>;
  onApproveWorkflowPlan?: (taskId: string) => Promise<void> | void;
  onReviseWorkflowPlan?: (taskId: string, feedback: string) => Promise<void> | void;
  onRetryWorkflowStep?: (taskId: string, stepId: string) => void;
  onRetryTurn?: (messageId: string) => void;
  onOpenMailInbox?: () => void;
  onRetryMessage?: (msg: ChatMessage) => void;
  onLoadFullEvidence?: (chunkId: string) => Promise<ChatRagEvidence | null>;
  isDetailsOpen?: boolean;
  onOpenExecutionTrace?: (message: ChatMessage) => void;
}

const ChatMessageItem: React.FC<ChatMessageItemProps> = React.memo(({
  msg,
  selectedModel,
  copiedId,
  onCopy,
  onSend,
  workflows,
  onApproveWorkflowPlan,
  onReviseWorkflowPlan,
  onRetryWorkflowStep,
  onRetryTurn,
  onOpenMailInbox,
  onRetryMessage,
  onLoadFullEvidence,
  isDetailsOpen,
  onOpenExecutionTrace,
}) => {
  const isUser = msg.role === 'user';
  const parts = useMemo(() => parseMarkdownContent(msg.content), [msg.content]);

  return (
    <div className="w-full flex flex-col gap-2" data-testid="chat-message" data-role={msg.role}>
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
              {isUser ? 'Bạn' : 'F-Cowork AI'}
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

        {!isUser && msg.activities && msg.activities.length > 0 && (
          <AgentActivityTimeline
            activities={msg.activities}
            generationStatus={msg.generationStatus}
            completedAt={msg.completedAt}
            isDetailsOpen={isDetailsOpen}
            onOpenDetails={onOpenExecutionTrace ? () => onOpenExecutionTrace(msg) : undefined}
          />
        )}

        <div data-testid={!isUser ? 'assistant-message-content' : undefined}>
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
              return (
                <CodeBlockItem
                  key={pIdx}
                  part={part}
                  pIdx={pIdx}
                  msgId={msg.id}
                  copiedId={copiedId}
                  onCopy={onCopy}
                  isStreaming={msg.isStreaming}
                  isLastPart={isLastPart}
                />
              );
            }
          })}

          {/* Caret fallback if message is streaming and content is empty */}
          {msg.isStreaming && parts.length === 0 && (
            <span className="inline-block w-2 h-4 bg-[#d97757] animate-caret align-middle select-none shadow-[0_0_8px_rgba(217,119,87,0.8)]" />
          )}
        </div>
        {msg.mailScan && ['succeeded', 'partial', 'failed'].includes(msg.mailScan.status) && (
          <MailScanCard scan={msg.mailScan} onOpenMailInbox={onOpenMailInbox} />
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

        {!isUser && msg.citations && msg.citations.length > 0 && (() => {
          const uniqueFiles = Array.from(
            new Set(msg.citations.map((c) => c.documentTitle).filter(Boolean))
          );
          if (uniqueFiles.length === 0) return null;
          return (
            <div className="mt-3 flex flex-wrap gap-2" aria-label="Tài liệu đã trích xuất">
              {uniqueFiles.map((docTitle) => (
                <span
                  key={docTitle}
                  className="inline-flex items-center gap-1.5 rounded-full border border-[#5a5149] bg-[#2b2925] px-3 py-1 text-xs text-zinc-200"
                >
                  <FileText className="h-3.5 w-3.5 text-[#d97757]" />
                  <span className="font-medium">{docTitle}</span>
                </span>
              ))}
            </div>
          );
        })()}

        {!isUser && (msg.ragEvidence || msg.retrievalStatus) && (
          <RagEvidencePanel
            evidence={msg.ragEvidence ?? []}
            retrievalStatus={msg.retrievalStatus}
            onLoadFullEvidence={onLoadFullEvidence}
          />
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
            onClick={() => onCopy(msg.content, msg.id)}
            className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
            title="Sao chép câu trả lời"
          >
            {copiedId === msg.id ? (
              <Check className="w-3.5 h-3.5 text-emerald-400" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
          </button>

          <button
            className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
            title="Phản hồi tốt"
          >
            <ThumbsUp className="w-3.5 h-3.5" />
          </button>

          <button
            className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
            title="Phản hồi chưa tốt"
          >
            <ThumbsDown className="w-3.5 h-3.5" />
          </button>

          <button
            onClick={() => {
              if (onRetryTurn) {
                onRetryTurn(msg.id);
              } else if (onRetryMessage) {
                onRetryMessage(msg);
              }
            }}
            className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
            title="Thử lại"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>

          <button
            className="p-1.5 hover:text-zinc-200 hover:bg-[#2a2824] rounded-lg transition-colors cursor-pointer"
            title="Chia sẻ cuộc trò chuyện"
          >
            <Share2 className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
});

export const ChatStreamView: React.FC<ChatStreamViewProps> = ({
  messages,
  isTranscriptLoading = false,
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
  onLoadFullEvidence,
  activeProject,
  projects,
  onSelectProject,
  onOpenMailInbox,
  selectedTraceMessageId,
  onOpenExecutionTrace,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef<boolean>(false);
  const prevLastMessageRef = useRef<ChatMessage | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState<boolean>(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

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

  const handleCopy = useCallback((text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  const handleRetryMessage = useCallback((msg: ChatMessage) => {
    if (onRetryTurn) {
      onRetryTurn(msg.id);
    } else {
      const idx = messages.findIndex((m) => m.id === msg.id);
      const userMsg = idx > 0 ? messages[idx - 1] : null;
      if (userMsg && userMsg.role === 'user') {
        onSend(userMsg.content);
      }
    }
  }, [messages, onRetryTurn, onSend]);

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden relative" data-testid="chat-stream">
      {/* Scrollable Messages Stream Container */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 w-full overflow-y-auto custom-scrollbar select-text"
      >
        {/* Inner Centered Messages Wrapper (Slightly wider max-w-4xl md:max-w-5xl) */}
        <div className="max-w-4xl md:max-w-5xl w-full mx-auto px-4 py-6 space-y-6">
          {isTranscriptLoading && messages.length === 0 && (
            <div
              data-testid="chat-transcript-loading"
              role="status"
              className="rounded-2xl border border-[#2d2b27] bg-[#1e1d1a] px-4 py-6 text-sm text-zinc-400"
            >
              Đang tải cuộc trò chuyện…
            </div>
          )}
          {messages.map((msg) => (
            <ChatMessageItem
              key={msg.id}
              msg={msg}
              selectedModel={selectedModel}
              copiedId={copiedId}
              onCopy={handleCopy}
              onSend={onSend}
              workflows={workflows}
              onApproveWorkflowPlan={onApproveWorkflowPlan}
              onReviseWorkflowPlan={onReviseWorkflowPlan}
              onRetryWorkflowStep={onRetryWorkflowStep}
              onRetryTurn={onRetryTurn}
              onOpenMailInbox={onOpenMailInbox}
              onRetryMessage={handleRetryMessage}
              onLoadFullEvidence={onLoadFullEvidence}
              isDetailsOpen={selectedTraceMessageId === msg.id}
              onOpenExecutionTrace={onOpenExecutionTrace}
            />
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Floating Scroll to Bottom Button if user scrolled up */}
      {showScrollToBottom && (
        <div className="absolute bottom-24 right-8 z-20 select-none">
          <button
            onClick={scrollToBottom}
            className="p-2 bg-[#2c2a26] hover:bg-[#383632] text-zinc-200 border border-zinc-700/60 rounded-full shadow-lg transition-all cursor-pointer flex items-center justify-center"
            title="Cuộn xuống dưới"
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
            <span>Dừng tạo phản hồi</span>
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
