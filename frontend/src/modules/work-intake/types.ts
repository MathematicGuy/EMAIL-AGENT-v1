// Wire types for Module 1 (Work Intake / Agent Orchestrator).
// Transcribed from apps/backend/app/modules/work_intake/types.py and
// apps/backend/app/contracts/envelope.py. The wire format is snake_case, so
// these interfaces stay snake_case — do not camelCase them.
//
// ponytail: only the slice this panel actually sends or renders is
// transcribed. AgentTask / AgentResult / WorkerProgress / outbox contracts are
// executor-to-executor traffic that never reaches the browser.

export type ModuleId =
  | 'module_1'
  | 'module_2'
  | 'module_3'
  | 'module_4'
  | 'module_5'
  | 'ui'
  | 'system';

export type ExecutorModule = 'module_2' | 'module_3' | 'module_4';

export type TaskStatus =
  | 'RECEIVED'
  | 'CLARIFICATION_REQUIRED'
  | 'PLANNED'
  | 'AWAITING_PLAN_APPROVAL'
  | 'RUNNING'
  | 'AWAITING_ACTION_APPROVAL'
  | 'PAUSED'
  | 'COMPLETED'
  | 'PARTIAL'
  | 'FAILED'
  | 'CANCELLED';

export type StepStatus =
  | 'PENDING'
  | 'READY'
  | 'DISPATCHED'
  | 'RUNNING'
  | 'WAITING_INPUT'
  | 'WAITING_APPROVAL'
  | 'SUCCEEDED'
  | 'PARTIAL'
  | 'FAILED'
  | 'TIMED_OUT'
  | 'BLOCKED'
  | 'CANCELLED'
  | 'SKIPPED';

export type TerminalTaskStatus =
  | 'COMPLETED'
  | 'PARTIAL'
  | 'FAILED'
  | 'CANCELLED';

export type PlanApprovalMode =
  | 'REQUIRE_INTERACTIVE'
  | 'PREAPPROVED_TEMPLATE'
  | 'AUTO_WITHIN_POLICY';

export type ControlAction = 'PAUSE' | 'RESUME' | 'CANCEL';

export type TriggerType = 'user' | 'manual' | 'one_time' | 'recurring';

export type ResourceAccess =
  | 'NONE'
  | 'SNAPSHOT_READ'
  | 'EXTERNAL_READ'
  | 'EXTERNAL_WRITE';

export type Mutation = 'NONE' | 'WRITE';

export type OnFailure = 'FAIL_TASK' | 'CONTINUE' | 'REPLAN' | 'USE_FALLBACK';

export type ErrorCategory =
  | 'VALIDATION'
  | 'PERMISSION'
  | 'MISSING_INPUT'
  | 'TRANSIENT'
  | 'TIMEOUT'
  | 'DEPENDENCY'
  | 'CONFLICT'
  | 'INTERNAL';

/** Shared error shape from shared-contracts-v1 §6. */
export interface ErrorBody {
  error_code: string;
  category: ErrorCategory;
  retryable: boolean;
  user_message: string;
  origin_module: ModuleId;
  failed_requirement: string | null;
  details: Record<string, unknown>;
}

export interface MessageEnvelope {
  schema_version: '1.0';
  message_id: string;
  correlation_id: string;
  causation_id?: string | null;
  request_id?: string | null;
  task_id?: string | null;
  run_id?: string | null;
  plan_id?: string | null;
  plan_version?: number | null;
  step_id?: string | null;
  actor_id: string;
  project_id: string;
  workspace_id: string;
  producer: ModuleId;
  consumer: ModuleId;
  created_at: string;
  deadline?: string | null;
  idempotency_key?: string | null;
  logical_payload_hash?: string | null;
  trace_context?: Record<string, unknown>;
}

export interface SourceSnapshotRef {
  ref_id: string;
  checksum: string;
  provider_id?: string | null;
  source_id?: string | null;
  source_version?: string | null;
  media_type?: string | null;
  size_bytes?: number | null;
  actor_id?: string | null;
  project_id?: string | null;
  workspace_id?: string | null;
  task_id?: string | null;
  run_id?: string | null;
  producer_module?: ModuleId | null;
  created_at?: string | null;
  expires_at?: string | null;
  provenance?: Record<string, unknown>;
}

export interface StepDependency {
  step_id: string;
  required: boolean;
  accepted_states: StepStatus[];
}

export interface CompletionCriterion {
  criterion_id: string;
  rule: string;
  description?: string | null;
  mandatory: boolean;
}

export interface RetryPolicy {
  max_retries: number;
  retryable_categories: string[];
  backoff_seconds: number;
}

export interface ContextRequirement {
  scope_type: string;
  scope_id: string;
  required_memory_types: string[];
  token_budget: number;
  freshness_seconds?: number | null;
}

export interface PlanStep {
  step_id: string;
  task_type: string;
  assigned_module: ExecutorModule;
  capability_id: string;
  capability_version: string;
  required: boolean;
  allow_partial: boolean;
  depends_on: StepDependency[];
  input_refs: SourceSnapshotRef[];
  context_requirement: ContextRequirement;
  expected_output_schema: Record<string, unknown>;
  completion_criteria: CompletionCriterion[];
  timeout_seconds: number;
  retry_policy: RetryPolicy;
  resource_access: ResourceAccess;
  mutation: Mutation;
  approval_required: boolean;
  on_failure: OnFailure;
  fallback_step_id?: string | null;
  operation_revision: number;
  operation_key: string;
  idempotency_key: string;
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
}

export type GroundingStatus = 'GROUNDED' | 'GENERATED_SPECULATION';
export type GroundingSourceKind = 'DOCUMENT' | 'MEMORY';

export interface GroundingSource {
  marker: number;
  kind: GroundingSourceKind;
  reference_id: string;
  display_name: string;
  source_ref_id?: string | null;
  locator: Record<string, unknown>;
  excerpt?: string | null;
}

export interface GroundingSummary {
  status: GroundingStatus;
  label: string;
  source_coverage?: number | null;
  sources: GroundingSource[];
}

export interface ArtifactGrounding {
  artifact_ref_id: string;
  grounding: GroundingSummary;
}

export interface Finding {
  finding_id: string;
  importance: 'IMPORTANT' | 'SUPPORTING';
  statement: string;
  value?: unknown;
  unit?: string | null;
  evidence_refs: string[];
  unverified_reason?: string | null;
  origin_task_id?: string | null;
  origin_step_id?: string | null;
  conflict_group_id?: string | null;
  conflicts_with?: string[];
}

export interface QueryEnvelope extends MessageEnvelope {
  client_request_id: string;
  session_id?: string | null;
  query: string;
  attachment_refs?: SourceSnapshotRef[];
  source_refs?: SourceSnapshotRef[];
  output_preference?: Record<string, unknown>;
  trigger_type: TriggerType;
  locale: string;
  timezone: string;
  business_deadline?: string | null;
}

export interface TaskView {
  schema_version: '1.0';
  task_id: string;
  run_id: string;
  request_id: string;
  client_request_id: string;
  status: TaskStatus;
  state_version: number;
  pause_requested: boolean;
  cancellation_generation: number;
  current_plan_version: number | null;
  actor_id: string;
  project_id: string;
  workspace_id: string;
  created_at: string;
  updated_at: string;
}

export interface RunView {
  run_id: string;
  task_id: string;
  created_at: string;
  active_budget_used_ms: number;
  absolute_deadline: string;
  replan_count: number;
  context_degraded: boolean;
  memory_degraded: boolean;
  checkpoint: Record<string, unknown>;
}

export interface StepView {
  step_id: string;
  task_type: string;
  assigned_module: ExecutorModule;
  capability_id: string;
  capability_version: string;
  state: StepStatus;
  required: boolean;
  allow_partial: boolean;
  accepted_partial: boolean;
  current_attempt: number;
  current_job_id: string | null;
  operation_revision: number;
  depends_on: StepDependency[];
  completion_criteria: CompletionCriterion[];
  progress_percent?: number | null;
  updated_at: string;
}

export interface PlanView {
  plan_id: string;
  plan_version: number;
  objective: string;
  scope: string[];
  out_of_scope: string[];
  constraints: string[];
  assumptions: string[];
  risks: string[];
  completion_criteria: CompletionCriterion[];
  approval_mode?: PlanApprovalMode | null;
  approved: boolean;
  steps: StepView[];
  plan_steps: PlanStep[];
  // ponytail: PlanView on the backend does not carry the template hash that
  // POST /plan:approve must echo back, so the panel falls back to an operator
  // typed value when the server omits it.
  query_template_hash?: string | null;
}

export interface FinalResponse {
  schema_version: '1.0';
  task_id: string;
  run_id: string;
  terminal_status: TerminalTaskStatus;
  summary: string;
  findings: Finding[];
  artifact_refs: SourceSnapshotRef[];
  artifact_grounding?: ArtifactGrounding[];
  resource_refs: SourceSnapshotRef[];
  evidence_refs: EvidenceReference[];
  assumptions: string[];
  unresolved: string[];
  context_degraded: boolean;
  memory_degraded: boolean;
  error?: ErrorBody | null;
}

export interface ClarificationQuestion {
  question_id: string;
  question: string;
  field: string;
  required: boolean;
  options: string[];
}

export interface ClarificationRequest {
  clarification_id: string;
  task_id: string;
  round: number;
  questions: ClarificationQuestion[];
  expires_at: string;
}

export interface ClarificationAnswer {
  question_id: string;
  answer: string;
}

export interface PlanPatchRequest {
  schema_version: '1.0';
  command_id: string;
  expected_plan_version: number;
  steps: PlanStep[];
  reason?: string | null;
}

/** Body of `POST /v1/tasks/{task_id}/plan:revise`. */
export interface PlanRevisionRequest {
  schema_version: '1.0';
  command_id: string;
  expected_plan_version: number;
  feedback: string;
}

/** Body of `POST /v1/tasks/{task_id}/steps/{step_id}:retry`. */
export interface StepRetryRequest {
  schema_version: '1.0';
  command_id: string;
  expected_plan_version: number;
  expected_state_version: number;
}

export interface PlanApproveRequest {
  schema_version: '1.0';
  command_id: string;
  plan_version: number;
  query_template_hash: string;
  approval_mode: PlanApprovalMode;
  actor_id: string;
}

export interface ControlCommand extends MessageEnvelope {
  command: ControlAction;
  command_id: string;
  reason?: string | null;
  expected_state_version?: number | null;
}

export interface RunEvent extends MessageEnvelope {
  sequence: number;
  aggregate_id: string;
  aggregate_type: 'TASK' | 'RUN' | 'STEP';
  state_version: number;
  event_type: string;
  previous_state: string | null;
  current_state: string;
  progress_percent?: number | null;
  occurred_at: string;
  error?: ErrorBody | null;
}

export interface RunEventPage {
  events: RunEvent[];
  next_sequence: number;
  has_more: boolean;
}

export interface StepResult {
  step_id: string;
  result_json: string;
}

/** One edge of a calendar event, as the executor will send it. */
export interface CalendarEventTime {
  date_time?: string | null;
  date?: string | null;
  time_zone: string;
}

/**
 * Where one event field came from. `assumed` marks a field the run derived
 * rather than read, so the approver is never shown a guess as a source.
 */
export interface CalendarSourceCitation {
  field: 'summary' | 'description' | 'location' | 'start' | 'end';
  result_index: number;
  url: string;
  assumed: boolean;
}

/**
 * The executor's `ActionPreview`, passed through by Module 1 verbatim.
 *
 * `account_label` is a display label; `calendar_id` is the calendar the event is
 * actually written to. Both are rendered because they are configured separately
 * and nothing compares them.
 */
export interface ActionPreview {
  action_id: string;
  capability_id: string;
  payload_hash: string;
  account_label: string;
  expires_at: string;
  idempotency_key?: string | null;
  calendar_id?: string | null;
  summary?: string | null;
  start?: CalendarEventTime | null;
  end?: CalendarEventTime | null;
  all_day?: boolean | null;
  location?: string | null;
  source_citations?: CalendarSourceCitation[] | null;
}

/** The parked action from `GET /v1/tasks/{task_id}`. */
export interface PendingActionApproval {
  action_id: string;
  step_id: string;
  capability_id: string;
  payload_hash: string;
  scope: string;
  expires_at: string;
  state_version: number;
  preview: ActionPreview;
}

export interface ApprovalDecision {
  schema_version: '1.0';
  approval_id: string;
  action_id: string;
  decision: 'approved' | 'denied';
  approver_id: string;
  actor_id: string;
  target: Record<string, unknown>;
  payload_hash: string;
  scope: string;
  state_version: number;
  issued_at: string;
  expires_at: string;
}

/** Response body of `GET /v1/tasks/{task_id}`. */
export interface TaskDetail {
  task: TaskView;
  run: RunView | null;
  plan: PlanView | null;
  steps: StepView[];
  step_results?: StepResult[] | null;
  // Optional because the route contract only guarantees task/run/plan/steps.
  final_response?: FinalResponse | null;
  clarification?: ClarificationRequest | null;
  action_approval?: PendingActionApproval | null;
}

export const TERMINAL_TASK_STATUSES: readonly TaskStatus[] = [
  'COMPLETED',
  'PARTIAL',
  'FAILED',
  'CANCELLED'
];

export function isTerminalStatus(status: TaskStatus): boolean {
  return TERMINAL_TASK_STATUSES.includes(status);
}

export const DONE_STEP_STATES: readonly StepStatus[] = [
  'SUCCEEDED',
  'PARTIAL',
  'FAILED',
  'TIMED_OUT',
  'CANCELLED',
  'SKIPPED'
];
