// Local TS mirror of the shapes Module 2's backend returns (see
// docs/module-requirements/shared-contracts-v1.md §11, §14 and
// module-2-document-data-intelligence.md §10). This is a UI-only view model,
// not a runtime validator — the backend is the schema source of truth.

export type Operation = 'parse' | 'extract' | 'analyze' | 'query';

export type AgentResultStatus =
  | 'SUCCEEDED'
  | 'PARTIAL'
  | 'NEEDS_INPUT'
  | 'RETRYABLE_FAILURE'
  | 'PERMANENT_FAILURE'
  | 'CANCELLED';

export interface ErrorBody {
  error_code: string;
  category: string;
  retryable: boolean;
  user_message: string;
  failed_requirement?: string | null;
}

export interface EvidenceReference {
  evidence_id: string;
  source_ref_id: string;
  source_id: string;
  source_version: string;
  checksum: string;
  locator: Record<string, unknown>;
  accessed_at: string;
  excerpt?: string | null;
  relevance_score?: number | null;
}

export interface PerSourceResult {
  ref_id: string;
  status: 'SUCCEEDED' | 'FAILED';
  error?: ErrorBody | null;
}

export interface AgentResultMetrics {
  duration_ms: number;
  worker_version: string;
  started_at: string;
  completed_at: string;
}

export interface AgentResult {
  job_id: string;
  status: AgentResultStatus;
  output: Record<string, unknown>;
  evidence_refs: EvidenceReference[];
  validation_issues: unknown[];
  error?: ErrorBody | null;
  metrics: AgentResultMetrics;
}

export interface DemoScenario {
  id: string;
  fixtureName: string;
  operation: Operation;
  description: string;
  result: AgentResult;
}
