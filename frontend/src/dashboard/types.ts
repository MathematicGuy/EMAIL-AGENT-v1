import type {
  ArtifactGrounding,
  RunEvent,
  SourceSnapshotRef,
  TaskDetail,
} from '../modules/work-intake/types';
import type { AssistantModelId } from '../modules/work-intake/assistantApi';

/** `unavailable` means the last state is stale, never that progress stopped. */
export type WorkflowConnectionState = 'live' | 'unavailable';

/** Server-owned view of one Module 1 task, projected for the chat card. */
export interface TaskWorkflow {
  taskId: string;
  detail: TaskDetail | null;
  events: RunEvent[];
  phase: string;
  connectionState: WorkflowConnectionState;
  lastEventSequence: number;
}

export type SidebarState = 'collapsed' | 'expanded';

export interface ModelOption {
  id: AssistantModelId;
  name: string;
  version: string;
  description: string;
  badge?: string;
  icon?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  isStreaming?: boolean;
  attachmentRefs?: SourceSnapshotRef[];
  artifactRefs?: SourceSnapshotRef[];
  artifactGrounding?: ArtifactGrounding[];
  taskId?: string;
  taskStatus?: string;
  quickActions?: string[];
  codeBlocks?: {
    language: string;
    code: string;
  }[];
  citations?: ChatCitation[];
  ragEvidence?: ChatRagEvidence[];
  retrievalStatus?: ChatRetrievalStatus;
}

export type ChatRetrievalStatus = 'success' | 'no_results' | 'timeout' | 'unavailable';

export interface ChatRagEvidence {
  source: 'company_knowledge' | 'project_document';
  retrievalStatus: 'success';
  chunkId: string;
  documentId: string;
  documentTitle: string;
  section: string | null;
  sourceUrl: string | null;
  relevanceScore: number;
  rerankScore: number | null;
  preview: string;
  content: string;
}

export interface ChatCitation {
  citationId: string;
  projectId: string;
  documentId: string;
  documentTitle: string;
  section?: string;
  pageStart: number;
  pageEnd: number;
  unavailable?: boolean;
}

export interface ChatComposerAttachment {
  id: string;
  name: string;
  mediaType: string;
  sizeBytes: number;
  status: 'hashing' | 'uploading' | 'processing' | 'ready' | 'error' | 'deleting';
  error?: string;
  documentId?: string;
}

export interface RecentChat {
  id: string;
  title: string;
  projectId?: string;
  date?: string;
  unread?: boolean;
  category?: 'recent' | 'product' | 'project';
}

export interface ThemeOption {
  id: string;
  name: string;
  bgHex: string;
  cardHex: string;
  accentHex: string;
}

export interface ExecStep {
  id: string;
  name: string;
  status: 'pending' | 'running' | 'completed' | 'waiting_approval';
  details: string;
}

export interface ContextSource {
  id: string;
  name: string;
  type: 'pdf' | 'docx' | 'code' | 'api';
  size: string;
}

export interface CoworkArtifact {
  id: string;
  title: string;
  type: 'markdown' | 'json' | 'diagram' | 'slides';
  content: string;
  version: string;
  grounding?: import('../modules/work-intake/types').GroundingSummary;
}

export interface CoworkTask {
  id: string;
  title: string;
  goal: string;
  status: 'draft' | 'running' | 'waiting_approval' | 'completed';
  progress: number;
  steps: ExecStep[];
  contextSources: ContextSource[];
  artifact?: CoworkArtifact;
}
