import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE_URL } from '../../lib/apiConfig';
import type {
  ChatCitation,
  ChatComposerAttachment,
  ChatMessage,
  RecentChat,
  TaskWorkflow,
} from '../types';

interface ChatSession {
  session_id: string;
  project_id: string;
}

interface ChatTurn {
  turn_id: string;
  user_message: string;
  assistant_message: string | null;
  created_at: string;
  citation_coordinates?: Array<Record<string, unknown>>;
}

interface SseEvent {
  event_type: string;
  text?: string;
  code?: string;
  safe_message?: string;
  source_id?: string;
  citation_scope?: string;
  project_id?: string;
  document_id?: string;
  document_title?: string;
  section?: string;
  page_start?: number;
  page_end?: number;
}

const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;
const ACCEPTED_FILE_EXTENSIONS = new Set(['docx', 'pdf']);

export function validateAttachmentFile(file: File): string | null {
  if (file.size > MAX_ATTACHMENT_BYTES) return `${file.name} exceeds the 25 MiB limit.`;
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  if (!ACCEPTED_FILE_EXTENSIONS.has(extension)) {
    return `${file.name} must be a PDF or DOCX document.`;
  }
  return null;
}

function timestamp(value?: string): string {
  return new Date(value ?? Date.now()).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}

function citationFromEvent(event: SseEvent): ChatCitation | null {
  if (
    event.citation_scope !== 'project_document' ||
    !event.source_id ||
    !event.project_id ||
    !event.document_id ||
    !event.document_title ||
    typeof event.page_start !== 'number' ||
    typeof event.page_end !== 'number'
  ) return null;
  return {
    citationId: event.source_id,
    projectId: event.project_id,
    documentId: event.document_id,
    documentTitle: event.document_title,
    section: event.section,
    pageStart: event.page_start,
    pageEnd: event.page_end,
  };
}

function citationFromCoordinate(value: Record<string, unknown>): ChatCitation | null {
  if (
    value.citation_scope !== 'project_document' ||
    typeof value.project_id !== 'string' ||
    typeof value.document_id !== 'string' ||
    typeof value.document_title !== 'string' ||
    typeof value.page_start !== 'number' ||
    typeof value.page_end !== 'number'
  ) return null;
  return {
    citationId: `${value.document_id}:${value.page_start}:${value.page_end}`,
    projectId: value.project_id,
    documentId: value.document_id,
    documentTitle: value.document_title,
    section: typeof value.section === 'string' ? value.section : undefined,
    pageStart: value.page_start,
    pageEnd: value.page_end,
  };
}

async function parseSse(
  response: Response,
  onEvent: (event: SseEvent) => void
): Promise<void> {
  if (!response.ok || !response.body) {
    let detail = `Chat request failed (HTTP ${response.status}).`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch { /* keep safe status message */ }
    throw new Error(detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() ?? '';
    for (const block of blocks) {
      const data = block.split('\n').find((line) => line.startsWith('data:'));
      if (data) onEvent(JSON.parse(data.slice(5).trim()) as SseEvent);
    }
    if (done) break;
  }
}

export function useStreamingChat(
  modelId = 'gemini-3.5-flash-lite',
  projectId = '',
  projectIds: string[] = projectId ? [projectId] : []
) {
  void modelId;
  void projectIds;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [recentChats, setRecentChats] = useState<RecentChat[]>([]);
  const [isHistoryLoading, setIsHistoryLoading] = useState(true);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [inputText, setInputText] = useState('');
  const [selectedAttachments, setSelectedAttachments] = useState<ChatComposerAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<'unknown' | 'online' | 'offline'>('unknown');
  const [workflows] = useState<Record<string, TaskWorkflow>>({});
  const abortRef = useRef<AbortController | null>(null);

  const refreshHistory = useCallback(async () => {
    if (!projectId) {
      setRecentChats([]);
      setIsHistoryLoading(false);
      return;
    }
    setIsHistoryLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions?project_id=${encodeURIComponent(projectId)}`
      );
      if (!response.ok) throw new Error();
      const payload = (await response.json()) as { sessions: ChatSession[] };
      setRecentChats(payload.sessions.map((session, index) => ({
        id: session.session_id,
        title: `Chat ${payload.sessions.length - index}`,
        projectId: session.project_id,
        category: 'recent',
      })));
      setApiStatus('online');
    } catch {
      setApiStatus('offline');
      setRecentChats([]);
    } finally {
      setIsHistoryLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    queueMicrotask(() => {
      setMessages([]);
      setActiveConversationId(null);
      setSelectedAttachments([]);
      void refreshHistory();
    });
  }, [projectId, refreshHistory]);

  const ensureSession = useCallback(async (): Promise<string> => {
    if (activeConversationId) return activeConversationId;
    if (!projectId) throw new Error('Select a Project before starting chat.');
    const response = await fetch(`${API_BASE_URL}/v1/cowork/chat/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId }),
    });
    if (!response.ok) throw new Error(`Could not create chat session (HTTP ${response.status}).`);
    const payload = (await response.json()) as ChatSession;
    setActiveConversationId(payload.session_id);
    return payload.session_id;
  }, [activeConversationId, projectId]);

  const selectAttachments = useCallback((files: File[]) => {
    if (!projectId) {
      setAttachmentError('Select a Project before uploading documents.');
      return;
    }
    window.dispatchEvent(new CustomEvent('open-project-documents'));
    for (const file of files) {
      const validation = validateAttachmentFile(file);
      const id = `upload_${crypto.randomUUID?.() ?? Date.now()}`;
      if (validation) {
        setAttachmentError(validation);
        continue;
      }
      setSelectedAttachments((current) => [...current, {
        id,
        name: file.name,
        mediaType: file.type,
        sizeBytes: file.size,
        status: 'uploading',
      }]);
      const form = new FormData();
      form.append('file', file);
      void fetch(
        `${API_BASE_URL}/v1/cowork/chat/projects/${encodeURIComponent(projectId)}/documents`,
        { method: 'POST', body: form }
      ).then(async (response) => {
        if (!response.ok) throw new Error(`Upload failed (HTTP ${response.status}).`);
        const document = (await response.json()) as { document_id: string; status: string };
        setSelectedAttachments((current) => current.map((item) =>
          item.id === id
            ? { ...item, documentId: document.document_id, status: 'uploaded' }
            : item
        ));
        setAttachmentError(null);
        window.dispatchEvent(new CustomEvent('project-documents-updated'));
      }).catch((cause) => {
        const message = cause instanceof Error ? cause.message : 'Upload failed.';
        setSelectedAttachments((current) => current.map((item) =>
          item.id === id ? { ...item, status: 'error', error: message } : item
        ));
        setAttachmentError(message);
      });
    }
  }, [projectId]);

  const removeAttachment = useCallback((attachmentId: string) => {
    setSelectedAttachments((current) => current.filter((item) => item.id !== attachmentId));
  }, []);

  const sendMessage = useCallback(async (override?: string) => {
    const text = (override ?? inputText).trim();
    if (!text || isGenerating) return;
    setInputText('');
    setIsGenerating(true);
    const now = Date.now();
    const userMessage: ChatMessage = {
      id: `user-${now}`,
      role: 'user',
      content: text,
      timestamp: timestamp(),
    };
    const assistantId = `assistant-${now}`;
    setMessages((current) => [...current, userMessage, {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: timestamp(),
      isStreaming: true,
    }]);
    const abort = new AbortController();
    abortRef.current = abort;
    try {
      const sessionId = await ensureSession();
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: sessionId,
            user_message: text,
            idempotency_key: `turn_${crypto.randomUUID?.() ?? now}`,
            document_ids: selectedAttachments
              .map((item) => item.documentId)
              .filter((documentId): documentId is string => Boolean(documentId)),
          }),
          signal: abort.signal,
        }
      );
      await parseSse(response, (event) => {
        if (event.event_type === 'delta' && event.text) {
          setMessages((current) => current.map((message) =>
            message.id === assistantId
              ? { ...message, content: message.content + event.text }
              : message
          ));
        } else if (event.event_type === 'memory_citation') {
          const citation = citationFromEvent(event);
          if (citation) setMessages((current) => current.map((message) =>
            message.id === assistantId
              ? { ...message, citations: [...(message.citations ?? []), citation] }
              : message
          ));
        } else if (event.event_type === 'error' && event.safe_message) {
          setMessages((current) => current.map((message) =>
            message.id === assistantId && !message.content
              ? { ...message, content: event.safe_message ?? '' }
              : message
          ));
        }
      });
      setMessages((current) => current.map((message) =>
        message.id === assistantId ? { ...message, isStreaming: false } : message
      ));
      setApiStatus('online');
      void refreshHistory();
    } catch (cause) {
      if ((cause as { name?: string }).name !== 'AbortError') {
        const error = cause instanceof Error ? cause.message : 'Chat backend unavailable.';
        setMessages((current) => current.map((message) =>
          message.id === assistantId ? { ...message, content: error, isStreaming: false } : message
        ));
        setApiStatus('offline');
      }
    } finally {
      abortRef.current = null;
      setIsGenerating(false);
    }
  }, [ensureSession, inputText, isGenerating, refreshHistory, selectedAttachments]);

  const loadExistingChat = useCallback(async (sessionId: string, loadedProjectId?: string) => {
    void loadedProjectId;
    setIsHistoryLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/messages`
      );
      if (!response.ok) throw new Error(`Could not load chat (HTTP ${response.status}).`);
      const payload = (await response.json()) as { turns: ChatTurn[] };
      setMessages(payload.turns.flatMap((turn) => {
        const citations = (turn.citation_coordinates ?? [])
          .map(citationFromCoordinate)
          .filter((item): item is ChatCitation => item !== null);
        return [
          { id: `${turn.turn_id}-user`, role: 'user' as const, content: turn.user_message, timestamp: timestamp(turn.created_at) },
          { id: `${turn.turn_id}-assistant`, role: 'assistant' as const, content: turn.assistant_message ?? '', timestamp: timestamp(turn.created_at), citations },
        ];
      }));
      setActiveConversationId(sessionId);
    } finally {
      setIsHistoryLoading(false);
    }
  }, []);

  const resetChat = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setActiveConversationId(null);
    setIsGenerating(false);
  }, []);

  const stopGeneration = useCallback(() => {
    abortRef.current?.abort();
    setIsGenerating(false);
  }, []);

  const retryTurn = useCallback((messageId: string) => {
    const index = messages.findIndex((message) => message.id === messageId);
    const previous = messages.slice(0, index).findLast((message) => message.role === 'user');
    if (previous) void sendMessage(previous.content);
  }, [messages, sendMessage]);

  const refreshWorkflow = useCallback(async (taskId: string) => { void taskId; }, []);
  const approveWorkflowPlan = useCallback(async (taskId: string) => { void taskId; }, []);
  const reviseWorkflowPlan = useCallback(
    async (taskId: string, feedback: string) => { void taskId; void feedback; },
    []
  );
  const retryWorkflowStep = useCallback(
    async (taskId: string, stepId: string) => { void taskId; void stepId; },
    []
  );

  return {
    workflows,
    refreshWorkflow,
    approveWorkflowPlan,
    reviseWorkflowPlan,
    retryWorkflowStep,
    retryTurn,
    pendingClarificationTurnId: null,
    messages,
    recentChats,
    isHistoryLoading,
    activeConversationId,
    isGenerating,
    inputText,
    setInputText,
    selectedAttachments,
    attachmentError,
    selectAttachments,
    removeAttachment,
    sendMessage,
    stopGeneration,
    resetChat,
    loadExistingChat,
    apiStatus,
  };
}
