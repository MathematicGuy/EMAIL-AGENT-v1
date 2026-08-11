import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { API_BASE_URL } from '../../lib/apiConfig';
import {
  AssistantApiError,
  cancelTurn,
  checkAssistantApi,
  createConversation,
  listConversationMessages,
  listConversations,
  sendConversationMessage,
  streamTurnEvents,
} from '../../modules/work-intake/assistantApi';
import type { AssistantModelId, AssistantScope } from '../../modules/work-intake/assistantApi';
import {
  approvePlan,
  getTask,
  listTaskEvents,
  retryStep,
  revisePlan,
} from '../../modules/work-intake/api';
import {
  DONE_STEP_STATES,
  isTerminalStatus,
} from '../../modules/work-intake/types';
import type {
  ArtifactGrounding,
  SourceSnapshotRef,
  TaskDetail,
} from '../../modules/work-intake/types';
import { uploadResource } from '../../modules/workspace/resourceApi';
import type {
  ChatComposerAttachment,
  ChatMessage,
  RecentChat,
  TaskWorkflow,
} from '../types';

interface ActiveTurn {
  conversationId: string;
  turnId: string;
}

function artifactGroundings(value: unknown): ArtifactGrounding[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is ArtifactGrounding =>
      typeof item === 'object' &&
      item !== null &&
      typeof (item as ArtifactGrounding).artifact_ref_id === 'string' &&
      typeof (item as ArtifactGrounding).grounding === 'object' &&
      (item as ArtifactGrounding).grounding !== null
  );
}

interface PendingClarification {
  turnId: string;
  /** Last event already rendered for this turn; clarification resumes reuse the turn. */
  afterSequence: number;
}

interface PendingAttachment extends ChatComposerAttachment {
  file: File;
  ref?: SourceSnapshotRef;
}

const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const MAX_ATTACHMENTS = 10;
const ACCEPTED_FILE_EXTENSIONS = new Set(['docx', 'md', 'pdf']);

function resourceRefs(value: unknown): SourceSnapshotRef[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is SourceSnapshotRef =>
      typeof item === 'object' &&
      item !== null &&
      typeof (item as SourceSnapshotRef).ref_id === 'string' &&
      typeof (item as SourceSnapshotRef).checksum === 'string'
  );
}

function quickActions(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

export function validateAttachmentFile(file: File): string | null {
  if (file.size > MAX_ATTACHMENT_BYTES) {
    return `${file.name} vượt giới hạn 10 MB.`;
  }
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  if (!ACCEPTED_FILE_EXTENSIONS.has(extension)) {
    return `${file.name} không thuộc định dạng PDF, DOCX hoặc Markdown.`;
  }
  return null;
}

function timestamp(value?: string): string {
  return new Date(value ?? Date.now()).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}

const PHASE_ANALYZING = 'Đang phân tích yêu cầu';
const PHASE_AWAITING_ANSWER = 'Đang chờ bạn trả lời';
const PHASE_AWAITING_APPROVAL = 'Đang chờ bạn phê duyệt plan';

const TERMINAL_PHASES: Record<string, string> = {
  COMPLETED: 'Đã hoàn thành',
  PARTIAL: 'Hoàn thành một phần',
  FAILED: 'Thất bại',
  CANCELLED: 'Đã huỷ',
};

/** Phase text comes from the server view only — never from a local timer. */
function activeStepPhase(capabilityId = ''): string {
  if (capabilityId.includes('parse')) return 'Đang đọc tài liệu…';
  if (capabilityId.includes('extract')) return 'Đang trích xuất thông tin…';
  if (capabilityId.includes('synthesize')) return 'Đang tổng hợp nội dung…';
  if (capabilityId.includes('analyze')) return 'Đang phân tích dữ liệu…';
  if (capabilityId.includes('artifact')) return 'Đang tạo tài liệu kết quả…';
  if (capabilityId.includes('validate')) return 'Đang kiểm tra kết quả…';
  return 'Đang thực hiện yêu cầu…';
}

function workflowPhase(detail: TaskDetail | null, fallback: string): string {
  if (!detail) return fallback;
  const status = detail.task.status;
  if (isTerminalStatus(status)) return TERMINAL_PHASES[status] ?? status;
  if (status === 'CLARIFICATION_REQUIRED') return PHASE_AWAITING_ANSWER;
  if (status === 'AWAITING_PLAN_APPROVAL' || status === 'AWAITING_ACTION_APPROVAL') {
    return PHASE_AWAITING_APPROVAL;
  }
  if (status === 'RUNNING') {
    const active = detail.steps.find(
      (step) => !DONE_STEP_STATES.includes(step.state)
    );
    return active ? activeStepPhase(active.capability_id) : 'Đang thực hiện yêu cầu…';
  }
  return PHASE_ANALYZING;
}

function emptyWorkflow(taskId: string): TaskWorkflow {
  return {
    taskId,
    detail: null,
    events: [],
    phase: PHASE_ANALYZING,
    connectionState: 'live',
    lastEventSequence: 0,
  };
}

export function useStreamingChat(
  modelId: AssistantModelId = 'gemini-3.5-flash-lite',
  projectId = 'demo-project',
  projectIds: string[] = [projectId]
) {
  const scope = useMemo<AssistantScope>(() => ({
    actorId: 'demo-user',
    projectId,
    workspaceId: 'demo-workspace',
  }), [projectId]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [recentChats, setRecentChats] = useState<RecentChat[]>([]);
  const [isHistoryLoading, setIsHistoryLoading] = useState(true);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    null
  );
  const [isGenerating, setIsGenerating] = useState(false);
  const [inputText, setInputText] = useState('');
  const [selectedAttachments, setSelectedAttachments] = useState<
    PendingAttachment[]
  >([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<'unknown' | 'online' | 'offline'>(
    'unknown'
  );
  const [workflows, setWorkflows] = useState<Record<string, TaskWorkflow>>({});
  const [pendingClarification, setPendingClarification] =
    useState<PendingClarification | null>(null);
  const pendingClarificationTurnId = pendingClarification?.turnId ?? null;
  const abortRef = useRef<AbortController | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const activeTurnRef = useRef<ActiveTurn | null>(null);
  const workflowsRef = useRef<Record<string, TaskWorkflow>>({});
  useEffect(() => {
    workflowsRef.current = workflows;
  }, [workflows]);

  /** Replaces the projection with the server truth; keeps the last one on failure. */
  const refreshWorkflow = useCallback(async (taskId: string) => {
    const previous = workflowsRef.current[taskId] ?? emptyWorkflow(taskId);
    try {
      const [detail, page] = await Promise.all([
        getTask({ taskId, scope }),
        listTaskEvents({
          taskId,
          afterSequence: previous.lastEventSequence,
          scope,
        }),
      ]);
      setWorkflows((current) => ({
        ...current,
        [taskId]: {
          taskId,
          detail,
          events: [...previous.events, ...page.events],
          phase: workflowPhase(detail, previous.phase),
          connectionState: 'live',
          lastEventSequence: Math.max(
            previous.lastEventSequence,
            page.next_sequence
          ),
        },
      }));
    } catch {
      setWorkflows((current) => ({
        ...current,
        [taskId]: { ...previous, connectionState: 'unavailable' },
      }));
    }
  }, [scope]);

  const trackWorkflow = useCallback(
    (taskId: string) => {
      setWorkflows((current) =>
        current[taskId] ? current : { ...current, [taskId]: emptyWorkflow(taskId) }
      );
      void refreshWorkflow(taskId);
    },
    [refreshWorkflow]
  );

  const approveWorkflowPlan = useCallback(
    async (taskId: string) => {
      const plan = workflowsRef.current[taskId]?.detail?.plan;
      if (!plan) return;
      await approvePlan({
        taskId,
        planVersion: plan.plan_version,
        queryTemplateHash: plan.query_template_hash ?? '',
        approvalMode: plan.approval_mode ?? 'REQUIRE_INTERACTIVE',
        scope,
      });
      await refreshWorkflow(taskId);
    },
    [refreshWorkflow]
  );

  const reviseWorkflowPlan = useCallback(
    async (taskId: string, feedback: string) => {
      const plan = workflowsRef.current[taskId]?.detail?.plan;
      if (!plan) return;
      await revisePlan({
        taskId,
        expectedPlanVersion: plan.plan_version,
        feedback,
        scope,
      });
      await refreshWorkflow(taskId);
    },
    [refreshWorkflow]
  );

  const retryWorkflowStep = useCallback(
    async (taskId: string, stepId: string) => {
      const detail = workflowsRef.current[taskId]?.detail;
      if (!detail?.plan) return;
      await retryStep({
        taskId,
        stepId,
        expectedPlanVersion: detail.plan.plan_version,
        expectedStateVersion: detail.task.state_version,
        scope,
      });
      await refreshWorkflow(taskId);
    },
    [refreshWorkflow]
  );

  const selectAttachments = useCallback((files: File[]) => {
    setAttachmentError(null);
    setSelectedAttachments((current) => {
      const available = Math.max(0, MAX_ATTACHMENTS - current.length);
      if (files.length > available) {
        setAttachmentError(`Mỗi tin nhắn nhận tối đa ${MAX_ATTACHMENTS} file.`);
      }
      const accepted: PendingAttachment[] = [];
      for (const file of files.slice(0, available)) {
        const error = validateAttachmentFile(file);
        if (error) {
          setAttachmentError(error);
          continue;
        }
        accepted.push({
          id: `attachment-${crypto.randomUUID()}`,
          file,
          name: file.name,
          mediaType: file.type || 'application/octet-stream',
          sizeBytes: file.size,
          status: 'ready',
        });
      }
      return [...current, ...accepted];
    });
  }, []);

  const removeAttachment = useCallback((attachmentId: string) => {
    setSelectedAttachments((current) =>
      current.filter((attachment) => attachment.id !== attachmentId)
    );
    setAttachmentError(null);
  }, []);

  const refreshHistory = useCallback(
    async (signal?: AbortSignal, showLoading = false) => {
      if (showLoading) setIsHistoryLoading(true);
      try {
        const conversationsByProject = await Promise.all(
          projectIds.map(async (id) => ({
            projectId: id,
            conversations: await listConversations({ ...scope, projectId: id }, signal),
          }))
        );
        setRecentChats(
          conversationsByProject.flatMap(({ projectId: conversationProjectId, conversations }) =>
            conversations.map((conversation) => ({
              id: conversation.conversation_id,
              title: conversation.title,
              projectId: conversationProjectId,
              date: new Date(conversation.last_activity_at).toLocaleDateString(),
              category: 'recent' as const,
            }))
          ).sort((a, b) => b.date!.localeCompare(a.date!))
        );
      } finally {
        if (showLoading && !signal?.aborted) setIsHistoryLoading(false);
      }
    },
    [projectIds, scope]
  );

  const sendMessage = useCallback(
    async (overrideText?: string) => {
      const textToSend = (overrideText || inputText).trim();
      if (!textToSend || isGenerating) return;

      setIsGenerating(true);
      setAttachmentError(null);

      const controller = new AbortController();
      abortRef.current = controller;
      let fullText = '';
      let assistantMessageId = '';
      let conversationIdForRecovery: string | null = null;
      const clarificationReply = pendingClarification;

      try {
        const attachmentRefs: SourceSnapshotRef[] = [];
        for (const attachment of selectedAttachments) {
          if (attachment.ref) {
            attachmentRefs.push(attachment.ref);
            continue;
          }
          setSelectedAttachments((current) =>
            current.map((item) =>
              item.id === attachment.id
                ? { ...item, status: 'uploading', error: undefined }
                : item
            )
          );
          try {
            const ref = await uploadResource(
              attachment.file,
              scope,
              controller.signal
            );
            attachmentRefs.push(ref);
            setSelectedAttachments((current) =>
              current.map((item) =>
                item.id === attachment.id
                  ? { ...item, ref, status: 'uploaded', error: undefined }
                  : item
              )
            );
          } catch (error) {
            const message =
              error instanceof Error ? error.message : 'Upload file thất bại.';
            setSelectedAttachments((current) =>
              current.map((item) =>
                item.id === attachment.id
                  ? { ...item, status: 'error', error: message }
                  : item
              )
            );
            setAttachmentError(message);
            throw error;
          }
        }

        const now = Date.now();
        assistantMessageId = `assistant-${now}`;
        setMessages((previous) => [
          ...previous,
          {
            id: `user-${now}`,
            role: 'user',
            content: textToSend,
            timestamp: timestamp(),
            attachmentRefs,
          },
          {
            id: assistantMessageId,
            role: 'assistant',
            content: '',
            timestamp: timestamp(),
            isStreaming: true,
          },
        ]);
        setInputText('');
        setSelectedAttachments([]);

        const conversationId =
          conversationIdRef.current ??
          (await createConversation(scope, controller.signal));
        conversationIdForRecovery = conversationId;
        conversationIdRef.current = conversationId;
        setActiveConversationId(conversationId);

        const accepted = await sendConversationMessage({
          conversationId,
          text: textToSend,
          modelId,
          attachmentRefs,
          ...(clarificationReply
            ? { replyToTurnId: clarificationReply.turnId }
            : {}),
          scope,
          signal: controller.signal,
        });
        setPendingClarification(null);
        activeTurnRef.current = {
          conversationId,
          turnId: accepted.turn_id,
        };
        setApiStatus('online');
        void refreshHistory().catch(() => undefined);

        const resumedClarification =
          clarificationReply?.turnId === accepted.turn_id;
        let lastEventSequence = resumedClarification
          ? clarificationReply.afterSequence
          : 0;
        await streamTurnEvents({
          conversationId,
          turnId: accepted.turn_id,
          afterSequence: lastEventSequence,
          scope,
          signal: controller.signal,
          onEvent: ({ id, event, data }) => {
            lastEventSequence = Math.max(lastEventSequence, id);
            if (event === 'assistant.delta') {
              fullText += String(data.delta ?? '');
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === assistantMessageId
                    ? { ...message, content: fullText, isStreaming: true }
                    : message
                )
              );
            } else if (
              event === 'assistant.completed' ||
              event === 'assistant.cancelled'
            ) {
              const artifactRefs = resourceRefs(data.artifact_refs);
              const artifactGrounding = artifactGroundings(
                data.artifact_grounding
              );
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === assistantMessageId
                    ? {
                        ...message,
                        isStreaming: false,
                        artifactRefs,
                        artifactGrounding,
                        taskId:
                          typeof data.task_id === 'string'
                            ? data.task_id
                            : message.taskId,
                      }
                    : message
                )
              );
            } else if (event === 'clarification.requested') {
              setPendingClarification({
                turnId: accepted.turn_id,
                afterSequence: lastEventSequence,
              });
              setIsGenerating(false);
              const actions = quickActions(data.quick_actions);
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === assistantMessageId
                    ? { ...message, quickActions: actions, isStreaming: false }
                    : message
                )
              );
              // This turn remains resumable on the backend. Abort only this local
              // SSE reader so sendMessage can finish instead of waiting for EOF.
              controller.abort();
            } else if (event === 'task.created' || event === 'task.status') {
              if (typeof data.task_id === 'string') trackWorkflow(data.task_id);
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === assistantMessageId
                    ? {
                        ...message,
                        taskId:
                          typeof data.task_id === 'string'
                            ? data.task_id
                            : message.taskId,
                        taskStatus:
                          typeof data.status === 'string'
                            ? data.status
                            : message.taskStatus,
                      }
                    : message
                )
              );
            } else if (event === 'assistant.failed') {
              throw new AssistantApiError(
                String(
                  data.user_message ??
                    'Assistant Runtime không hoàn thành được lượt này.'
                ),
                500
              );
            }
          },
        });

        setMessages((previous) =>
          previous.map((message) =>
            message.id === assistantMessageId
              ? { ...message, isStreaming: false }
              : message
          )
        );
      } catch (error) {
        if ((error as { name?: string }).name !== 'AbortError') {
          if (assistantMessageId && conversationIdForRecovery) {
            try {
              const persisted = await listConversationMessages(
                conversationIdForRecovery,
                scope,
                controller.signal
              );
              const assistant = persisted.findLast(
                (message) => message.role === 'assistant'
              );
              if (assistant) {
                setMessages((previous) =>
                  previous.map((message) =>
                    message.id === assistantMessageId
                      ? {
                          ...message,
                          content: assistant.content.text,
                          isStreaming: false,
                          artifactRefs: resourceRefs(
                            assistant.content.metadata.artifact_refs
                          ),
                          artifactGrounding: artifactGroundings(
                            assistant.content.metadata.artifact_grounding
                          ),
                          taskId:
                            typeof assistant.content.metadata.task_id ===
                            'string'
                              ? assistant.content.metadata.task_id
                              : message.taskId,
                          taskStatus:
                            typeof assistant.content.metadata.task_status ===
                            'string'
                              ? assistant.content.metadata.task_status
                              : message.taskStatus,
                          quickActions: quickActions(
                            assistant.content.metadata.quick_actions
                          ),
                        }
                      : message
                  )
                );
                setApiStatus('online');
                return;
              }
            } catch {
              // Fall through to the visible transport error below.
            }
          }
          const apiError =
            error instanceof AssistantApiError
              ? error
              : new AssistantApiError(
                  error instanceof Error ? error.message : 'Lỗi không xác định.'
                );
          setApiStatus(apiError.status === 0 ? 'offline' : 'online');

          const heading =
            apiError.status === 0
              ? 'Không thể kết nối backend local'
              : 'Backend trả về lỗi';
          const detail =
            apiError.status === 0
              ? `${apiError.message}\n\nHãy kiểm tra FastAPI tại ${API_BASE_URL} và chạy:\n` +
                '```bash\npnpm --filter backend dev\n```'
              : apiError.message;

          if (assistantMessageId) {
            setMessages((previous) =>
              previous.map((message) =>
                message.id === assistantMessageId
                  ? {
                      ...message,
                      content: `**${heading}**\n\n${detail}`,
                      isStreaming: false,
                    }
                  : message
              )
            );
          } else if (!attachmentError) {
            setAttachmentError(detail);
          }
        }
      } finally {
        void refreshHistory().catch(() => undefined);
        setIsGenerating(false);
        if (abortRef.current === controller) {
          abortRef.current = null;
          activeTurnRef.current = null;
        }
      }
    },
    [
      attachmentError,
      inputText,
      isGenerating,
      modelId,
      pendingClarification,
      refreshHistory,
      selectedAttachments,
      scope,
      trackWorkflow,
    ]
  );

  const stopGeneration = useCallback(() => {
    abortRef.current?.abort();
    const activeTurn = activeTurnRef.current;
    activeTurnRef.current = null;
    if (activeTurn) {
      void cancelTurn({
        ...activeTurn,
        scope,
      }).catch(() => undefined);
    }
    setIsGenerating(false);
    setMessages((previous) =>
      previous.map((message) =>
        message.isStreaming ? { ...message, isStreaming: false } : message
      )
    );
  }, [scope]);

  const resetChat = useCallback(() => {
    stopGeneration();
    conversationIdRef.current = null;
    setActiveConversationId(null);
    setMessages([]);
    setInputText('');
    setSelectedAttachments([]);
    setAttachmentError(null);
    setPendingClarification(null);
  }, [stopGeneration]);

  const loadExistingChat = useCallback(
    async (conversationId: string, conversationProjectId = projectId) => {
      stopGeneration();
      const controller = new AbortController();
      abortRef.current = controller;
      setIsHistoryLoading(true);
      setPendingClarification(null);

      try {
        const history = await listConversationMessages(
          conversationId,
          { ...scope, projectId: conversationProjectId },
          controller.signal
        );
        conversationIdRef.current = conversationId;
        setActiveConversationId(conversationId);
        const mapped = history.flatMap((message): ChatMessage[] =>
          message.role === 'user' || message.role === 'assistant'
            ? [
                {
                  id: message.message_id,
                  role: message.role,
                  content: message.content.text,
                  timestamp: timestamp(message.created_at),
                  isStreaming: false,
                  attachmentRefs: resourceRefs(
                    message.content.attachment_refs
                  ),
                  artifactRefs: resourceRefs(
                    message.content.metadata.artifact_refs
                  ),
                  artifactGrounding: artifactGroundings(
                    message.content.metadata.artifact_grounding
                  ),
                  taskId:
                    typeof message.content.metadata.task_id === 'string'
                      ? message.content.metadata.task_id
                      : undefined,
                  taskStatus:
                    typeof message.content.metadata.task_status === 'string'
                      ? message.content.metadata.task_status
                      : undefined,
                  quickActions: quickActions(
                    message.content.metadata.quick_actions
                  ),
                },
              ]
            : []
        );
        setMessages(mapped);
        mapped.forEach((msg) => {
          if (msg.taskId) {
            trackWorkflow(msg.taskId);
          }
        });
        setApiStatus('online');
      } catch (error) {
        if ((error as { name?: string }).name !== 'AbortError') {
          setApiStatus(
            error instanceof AssistantApiError && error.status > 0
              ? 'online'
              : 'offline'
          );
        }
      } finally {
        if (!controller.signal.aborted) setIsHistoryLoading(false);
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [projectId, scope, stopGeneration, trackWorkflow]
  );

  useEffect(() => {
    const controller = new AbortController();
    void checkAssistantApi(controller.signal).then((online) => {
      if (!controller.signal.aborted) {
        setApiStatus(online ? 'online' : 'offline');
      }
    });
    const initial = window.setTimeout(() => {
      void refreshHistory(controller.signal, true).catch(() => {
        if (!controller.signal.aborted) setIsHistoryLoading(false);
      });
    }, 0);
    return () => {
      clearTimeout(initial);
      controller.abort();
    };
  }, [refreshHistory]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // ponytail: poll every 2s for non-terminal tasks. Module 1 has no per-task
  // push channel yet; switch to SSE when /v1/tasks/{id}/events streams.
  //
  // CLARIFICATION_REQUIRED is not "still working" — nothing moves until the
  // user answers, and answering opens a new turn with its own stream. Polling
  // it forever produced an endless request loop against an idle backend.
  useEffect(() => {
    const pending = Object.values(workflows).filter(
      (workflow) =>
        !workflow.detail ||
        (!isTerminalStatus(workflow.detail.task.status) &&
          workflow.detail.task.status !== 'CLARIFICATION_REQUIRED' &&
          workflow.detail.task.status !== 'AWAITING_PLAN_APPROVAL' &&
          workflow.detail.task.status !== 'AWAITING_ACTION_APPROVAL')
    );
    if (pending.length === 0) return;
    const timer = window.setInterval(() => {
      for (const workflow of pending) void refreshWorkflow(workflow.taskId);
    }, 2000);
    return () => clearInterval(timer);
  }, [refreshWorkflow, workflows]);

  const retryTurn = useCallback(
    async (targetAssistantMessageId: string) => {
      if (isGenerating) return;
      const assistantIndex = messages.findIndex(
        (m) => m.id === targetAssistantMessageId
      );
      if (assistantIndex < 0) return;
      let userMsg: ChatMessage | undefined = messages[assistantIndex - 1];
      if (!userMsg || userMsg.role !== 'user') {
        userMsg = messages.slice(0, assistantIndex).findLast(
          (m) => m.role === 'user'
        );
      }
      if (!userMsg) return;

      setIsGenerating(true);
      setAttachmentError(null);

      const controller = new AbortController();
      abortRef.current = controller;
      let fullText = '';
      const assistantMessageId = targetAssistantMessageId;
      let conversationIdForRecovery: string | null = null;
      const clarificationReply = pendingClarification;

      try {
        const attachmentRefs: SourceSnapshotRef[] = userMsg.attachmentRefs || [];

        setMessages((previous) =>
          previous.map((message) =>
            message.id === assistantMessageId
              ? {
                  ...message,
                  content: '',
                  isStreaming: true,
                  taskId: undefined,
                  taskStatus: undefined,
                  artifactRefs: undefined,
                  artifactGrounding: undefined,
                  quickActions: undefined,
                }
              : message
          )
        );

        const conversationId =
          conversationIdRef.current ??
          (await createConversation(scope, controller.signal));
        conversationIdForRecovery = conversationId;
        conversationIdRef.current = conversationId;
        setActiveConversationId(conversationId);

        const accepted = await sendConversationMessage({
          conversationId,
          text: userMsg.content,
          modelId,
          attachmentRefs,
          ...(clarificationReply
            ? { replyToTurnId: clarificationReply.turnId }
            : {}),
          scope,
          signal: controller.signal,
        });
        setPendingClarification(null);
        activeTurnRef.current = {
          conversationId,
          turnId: accepted.turn_id,
        };
        setApiStatus('online');
        void refreshHistory().catch(() => undefined);

        const resumedClarification =
          clarificationReply?.turnId === accepted.turn_id;
        let lastEventSequence = resumedClarification
          ? clarificationReply.afterSequence
          : 0;
        await streamTurnEvents({
          conversationId,
          turnId: accepted.turn_id,
          afterSequence: lastEventSequence,
          scope,
          signal: controller.signal,
          onEvent: ({ id, event, data }) => {
            lastEventSequence = Math.max(lastEventSequence, id);
            if (event === 'assistant.delta') {
              fullText += String(data.delta ?? '');
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === assistantMessageId
                    ? { ...message, content: fullText, isStreaming: true }
                    : message
                )
              );
            } else if (
              event === 'assistant.completed' ||
              event === 'assistant.cancelled'
            ) {
              const artifactRefs = resourceRefs(data.artifact_refs);
              const artifactGrounding = artifactGroundings(
                data.artifact_grounding
              );
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === assistantMessageId
                    ? {
                        ...message,
                        isStreaming: false,
                        artifactRefs,
                        artifactGrounding,
                        taskId:
                          typeof data.task_id === 'string'
                            ? data.task_id
                            : message.taskId,
                      }
                    : message
                )
              );
            } else if (event === 'clarification.requested') {
              setPendingClarification({
                turnId: accepted.turn_id,
                afterSequence: lastEventSequence,
              });
              setIsGenerating(false);
              const actions = quickActions(data.quick_actions);
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === assistantMessageId
                    ? { ...message, quickActions: actions, isStreaming: false }
                    : message
                )
              );
              controller.abort();
            } else if (event === 'task.created' || event === 'task.status') {
              if (typeof data.task_id === 'string') trackWorkflow(data.task_id);
              setMessages((previous) =>
                previous.map((message) =>
                  message.id === assistantMessageId
                    ? {
                        ...message,
                        taskId:
                          typeof data.task_id === 'string'
                            ? data.task_id
                            : message.taskId,
                        taskStatus:
                          typeof data.status === 'string'
                            ? data.status
                            : message.taskStatus,
                      }
                    : message
                )
              );
            } else if (event === 'assistant.failed') {
              throw new AssistantApiError(
                String(
                  data.user_message ??
                    'Assistant Runtime không hoàn thành được lượt này.'
                ),
                500
              );
            }
          },
        });

        setMessages((previous) =>
          previous.map((message) =>
            message.id === assistantMessageId
              ? { ...message, isStreaming: false }
              : message
          )
        );
      } catch (error) {
        if ((error as { name?: string }).name !== 'AbortError') {
          if (assistantMessageId && conversationIdForRecovery) {
            try {
              const persisted = await listConversationMessages(
                conversationIdForRecovery,
                scope,
                controller.signal
              );
              const assistant = persisted.findLast(
                (message) => message.role === 'assistant'
              );
              if (assistant) {
                setMessages((previous) =>
                  previous.map((message) =>
                    message.id === assistantMessageId
                      ? {
                          ...message,
                          content: assistant.content.text,
                          isStreaming: false,
                          artifactRefs: resourceRefs(
                            assistant.content.metadata.artifact_refs
                          ),
                          artifactGrounding: artifactGroundings(
                            assistant.content.metadata.artifact_grounding
                          ),
                          taskId:
                            typeof assistant.content.metadata.task_id ===
                            'string'
                              ? assistant.content.metadata.task_id
                              : message.taskId,
                          taskStatus:
                            typeof assistant.content.metadata.task_status ===
                            'string'
                              ? assistant.content.metadata.task_status
                              : message.taskStatus,
                          quickActions: quickActions(
                            assistant.content.metadata.quick_actions
                          ),
                        }
                      : message
                  )
                );
                setApiStatus('online');
                return;
              }
            } catch {
              // Fall through
            }
          }
          const apiError =
            error instanceof AssistantApiError
              ? error
              : new AssistantApiError(
                  error instanceof Error ? error.message : 'Lỗi không xác định.'
                );
          setApiStatus(apiError.status === 0 ? 'offline' : 'online');

          const heading =
            apiError.status === 0
              ? 'Không thể kết nối backend local'
              : 'Backend trả về lỗi';
          const detail =
            apiError.status === 0
              ? `${apiError.message}\n\nHãy kiểm tra FastAPI tại ${API_BASE_URL} và chạy:\n` +
                '```bash\npnpm --filter backend dev\n```'
              : apiError.message;

          if (assistantMessageId) {
            setMessages((previous) =>
              previous.map((message) =>
                message.id === assistantMessageId
                  ? {
                      ...message,
                      content: `**${heading}**\n\n${detail}`,
                      isStreaming: false,
                    }
                  : message
              )
            );
          } else if (!attachmentError) {
            setAttachmentError(detail);
          }
        }
      } finally {
        void refreshHistory().catch(() => undefined);
        setIsGenerating(false);
        if (abortRef.current === controller) {
          abortRef.current = null;
          activeTurnRef.current = null;
        }
      }
    },
    [
      isGenerating,
      messages,
      modelId,
      pendingClarification,
      refreshHistory,
      scope,
      trackWorkflow,
    ]
  );

  return {
    workflows,
    refreshWorkflow,
    approveWorkflowPlan,
    reviseWorkflowPlan,
    retryWorkflowStep,
    retryTurn,
    pendingClarificationTurnId,
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
