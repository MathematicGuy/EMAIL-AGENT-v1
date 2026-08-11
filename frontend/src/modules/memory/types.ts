export type MemoryStatus =
  | 'PROPOSED'
  | 'ACTIVE'
  | 'REJECTED'
  | 'SUPERSEDED'
  | 'ARCHIVED'
  | 'DELETED'
  | 'INVALIDATED'
  | 'CONFLICT';

export type MemoryType =
  | 'working'
  | 'session'
  | 'project'
  | 'core'
  | 'template_rule'
  | 'experience'
  | 'source_index';

export interface MemoryRecord {
  memory_id: string;
  memory_type: MemoryType;
  content: string | null;
  scope_type: string;
  scope_id: string;
  authority: string;
  verification: string;
  status: MemoryStatus;
  version: number;
  source_refs: Record<string, unknown>[];
  conflict_group_id: string | null;
  updated_at: string;
}

export interface MemoryListResponse {
  items: MemoryRecord[];
  count: number;
}

export interface ContextSnapshot {
  context_snapshot_id: string;
  context_version: number;
  content_hash: string;
  policy_version: string;
  project_id: string;
  task_id: string | null;
  step_id: string | null;
  selected_memory_items: Array<{
    memory_id: string;
    memory_type: MemoryType;
    selection_reason: string;
    authority: string;
    freshness: number;
  }>;
  conflict_groups: Record<string, unknown>[];
  stale_warnings: string[];
  missing_context: MemoryType[];
  truncated: boolean;
  estimated_token_count: number;
  token_limit: number;
}

export interface ApiErrorShape {
  error_code?: string;
  user_message?: string;
}

export type MemoryClaimStatus =
  | 'PROPOSED'
  | 'ACTIVE'
  | 'CONFLICTED'
  | 'SUPERSEDED'
  | 'FORGOTTEN';

export interface MemoryClaim {
  claim_id: string;
  canonical_key: string;
  category: string;
  memory_type: MemoryType;
  content: string;
  confidence: number;
  sensitivity: 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL' | 'SECRET';
  status: MemoryClaimStatus;
  explicit: boolean;
  version: number;
  supersedes_claim_id: string | null;
  conflict_group_id: string | null;
  evidence_count: number;
  independent_run_count: number;
  decision_reason: string | null;
  updated_at: string;
}

export interface MemoryClaimListResponse {
  items: MemoryClaim[];
  count: number;
}

export interface MemoryClaimSource {
  evidence_id: string;
  observation_id: string;
  source_kind: string;
  source_id: string;
  source_run_id: string | null;
  stance: 'SUPPORTS' | 'CONTRADICTS';
  created_at: string;
}

export interface MemoryClaimSourcesResponse {
  claim_id: string;
  items: MemoryClaimSource[];
  count: number;
}

export interface ProjectMemorySummary {
  summary_id: string | null;
  content: string;
  sections: Array<{
    title: string;
    content: string;
    claim_ids: string[];
  }>;
  claim_ids: string[];
  version: number;
  freshness: 'EMPTY' | 'READY' | 'STALE';
  generated_at: string | null;
  pending_job_count: number;
}

export interface MemoryContextPreview {
  standalone_query: string;
  items: Array<{
    claim_id: string;
    memory_type: MemoryType;
    content: string;
    category: string;
    confidence: number;
    source_refs: Record<string, unknown>[];
    selection_reason: string;
    estimated_token_count: number;
  }>;
  excluded_claim_ids: string[];
  truncated: boolean;
  estimated_token_count: number;
  token_limit: number;
}
