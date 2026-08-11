import type {
  ApprovalDecision,
  ControlAction,
  ControlCommand,
  ErrorBody,
  MessageEnvelope,
  PendingActionApproval,
  PlanApprovalMode,
  PlanApproveRequest,
  PlanPatchRequest,
  PlanRevisionRequest,
  PlanStep,
  PlanView,
  QueryEnvelope,
  RunEventPage,
  SourceSnapshotRef,
  StepRetryRequest,
  TaskDetail,
  TaskView
} from './types';
import { API_BASE_URL } from '../../lib/apiConfig';

const SERVICE_IDENTITY = 'ui';

export interface WorkIntakeScope {
  actorId: string;
  projectId: string;
  workspaceId: string;
}

export class WorkIntakeApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

// ponytail: ids only have to be unique inside one browser session, so a uuid
// (with a Math.random fallback for non-secure contexts) is enough — no ULID
// dependency, no server-side reservation.
function newId(prefix: string): string {
  const uuid =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}_${uuid}`;
}

async function parseResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T | Partial<ErrorBody>;
  if (!response.ok) {
    const error = payload as Partial<ErrorBody>;
    throw new WorkIntakeApiError(
      error.user_message ?? 'Work intake request failed.',
      error.error_code ?? 'WORK_INTAKE_REQUEST_FAILED',
      response.status
    );
  }
  return payload as T;
}

function scopeParams(scope: WorkIntakeScope): URLSearchParams {
  return new URLSearchParams({
    actor_id: scope.actorId,
    project_id: scope.projectId,
    workspace_id: scope.workspaceId
  });
}

function mutatingHeaders(scope: WorkIntakeScope): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Service-Identity': SERVICE_IDENTITY,
    'X-Actor-Id': scope.actorId,
    'Idempotency-Key': newId('idem')
  };
}

function envelope(scope: WorkIntakeScope): MessageEnvelope {
  return {
    schema_version: '1.0',
    message_id: newId('msg'),
    correlation_id: newId('corr'),
    actor_id: scope.actorId,
    project_id: scope.projectId,
    workspace_id: scope.workspaceId,
    producer: 'ui',
    consumer: 'module_1',
    created_at: new Date().toISOString()
  };
}

export async function submitQuery(input: {
  query: string;
  scope: WorkIntakeScope;
  attachmentRefs?: SourceSnapshotRef[];
  outputPreference?: Record<string, unknown>;
}): Promise<TaskView> {
  const body: QueryEnvelope = {
    ...envelope(input.scope),
    client_request_id: newId('req'),
    query: input.query,
    attachment_refs: input.attachmentRefs ?? [],
    output_preference: input.outputPreference ?? {},
    trigger_type: 'user',
    locale: 'vi-VN',
    timezone: 'Asia/Ho_Chi_Minh'
  };
  const response = await fetch(`${API_BASE_URL}/v1/tasks`, {
    method: 'POST',
    headers: mutatingHeaders(input.scope),
    body: JSON.stringify(body)
  });
  return parseResponse<TaskView>(response);
}

export async function listTasks(scope: WorkIntakeScope): Promise<TaskView[]> {
  const response = await fetch(
    `${API_BASE_URL}/v1/tasks?${scopeParams(scope)}`,
    {
      headers: {
        'X-Service-Identity': SERVICE_IDENTITY,
        'X-Actor-Id': scope.actorId
      }
    }
  );
  return parseResponse<TaskView[]>(response);
}

export async function getTask(input: {
  taskId: string;
  scope: WorkIntakeScope;
}): Promise<TaskDetail> {
  const response = await fetch(
    `${API_BASE_URL}/v1/tasks/${input.taskId}?${scopeParams(input.scope)}`,
    {
      headers: {
        'X-Service-Identity': SERVICE_IDENTITY,
        'X-Actor-Id': input.scope.actorId
      }
    }
  );
  return parseResponse<TaskDetail>(response);
}

export async function approvePlan(input: {
  taskId: string;
  planVersion: number;
  queryTemplateHash: string;
  approvalMode: PlanApprovalMode;
  scope: WorkIntakeScope;
}): Promise<PlanView> {
  const body: PlanApproveRequest = {
    schema_version: '1.0',
    command_id: newId('cmd'),
    plan_version: input.planVersion,
    query_template_hash: input.queryTemplateHash,
    approval_mode: input.approvalMode,
    actor_id: input.scope.actorId
  };
  const response = await fetch(
    `${API_BASE_URL}/v1/tasks/${input.taskId}/plan:approve`,
    {
      method: 'POST',
      headers: mutatingHeaders(input.scope),
      body: JSON.stringify(body)
    }
  );
  return parseResponse<PlanView>(response);
}

/**
 * Decide one parked external action.
 *
 * Every binding field is copied from the pending approval the server handed
 * back; none of them is recomputed here. A client that derives `payload_hash`,
 * `scope` or `state_version` itself gets `APPROVAL_MISMATCH` for a decision the
 * human made correctly.
 */
export async function approveAction(input: {
  taskId: string;
  pending: PendingActionApproval;
  decision: 'approved' | 'denied';
  scope: WorkIntakeScope;
}): Promise<TaskView> {
  const body: ApprovalDecision = {
    schema_version: '1.0',
    approval_id: newId('apr'),
    action_id: input.pending.action_id,
    decision: input.decision,
    approver_id: input.scope.actorId,
    actor_id: input.scope.actorId,
    target: { capability_id: input.pending.capability_id },
    payload_hash: input.pending.payload_hash,
    scope: input.pending.scope,
    state_version: input.pending.state_version,
    issued_at: new Date().toISOString(),
    expires_at: input.pending.expires_at
  };
  const response = await fetch(
    `${API_BASE_URL}/v1/tasks/${input.taskId}/actions/${input.pending.action_id}:approve`,
    {
      method: 'POST',
      headers: mutatingHeaders(input.scope),
      body: JSON.stringify(body)
    }
  );
  return parseResponse<TaskView>(response);
}

export async function patchPlan(input: {
  taskId: string;
  expectedPlanVersion: number;
  steps: PlanStep[];
  reason: string | null;
  scope: WorkIntakeScope;
}): Promise<PlanView> {
  const body: PlanPatchRequest = {
    schema_version: '1.0',
    command_id: newId('cmd'),
    expected_plan_version: input.expectedPlanVersion,
    steps: input.steps,
    reason: input.reason
  };
  const response = await fetch(`${API_BASE_URL}/v1/tasks/${input.taskId}/plan`, {
    method: 'PATCH',
    headers: mutatingHeaders(input.scope),
    body: JSON.stringify(body)
  });
  return parseResponse<PlanView>(response);
}

export async function listTaskEvents(input: {
  taskId: string;
  afterSequence: number;
  scope: WorkIntakeScope;
}): Promise<RunEventPage> {
  const response = await fetch(
    `${API_BASE_URL}/v1/tasks/${input.taskId}/events?after_sequence=${input.afterSequence}`,
    {
      headers: {
        'X-Service-Identity': SERVICE_IDENTITY,
        'X-Actor-Id': input.scope.actorId
      }
    }
  );
  return parseResponse<RunEventPage>(response);
}

export async function revisePlan(input: {
  taskId: string;
  expectedPlanVersion: number;
  feedback: string;
  scope: WorkIntakeScope;
}): Promise<PlanView> {
  const body: PlanRevisionRequest = {
    schema_version: '1.0',
    command_id: newId('cmd'),
    expected_plan_version: input.expectedPlanVersion,
    feedback: input.feedback
  };
  const response = await fetch(
    `${API_BASE_URL}/v1/tasks/${input.taskId}/plan:revise`,
    {
      method: 'POST',
      headers: mutatingHeaders(input.scope),
      body: JSON.stringify(body)
    }
  );
  return parseResponse<PlanView>(response);
}

export async function retryStep(input: {
  taskId: string;
  stepId: string;
  expectedPlanVersion: number;
  expectedStateVersion: number;
  scope: WorkIntakeScope;
}): Promise<TaskDetail> {
  const body: StepRetryRequest = {
    schema_version: '1.0',
    command_id: newId('cmd'),
    expected_plan_version: input.expectedPlanVersion,
    expected_state_version: input.expectedStateVersion
  };
  const response = await fetch(
    `${API_BASE_URL}/v1/tasks/${input.taskId}/steps/${input.stepId}:retry`,
    {
      method: 'POST',
      headers: mutatingHeaders(input.scope),
      body: JSON.stringify(body)
    }
  );
  return parseResponse<TaskDetail>(response);
}

const CONTROL_SUFFIX: Record<ControlAction, string> = {
  PAUSE: ':pause',
  RESUME: ':resume',
  CANCEL: ':cancel'
};

export async function sendControlCommand(input: {
  taskId: string;
  action: ControlAction;
  expectedStateVersion: number | null;
  scope: WorkIntakeScope;
}): Promise<TaskView> {
  const body: ControlCommand = {
    ...envelope(input.scope),
    task_id: input.taskId,
    command: input.action,
    command_id: newId('cmd'),
    expected_state_version: input.expectedStateVersion
  };
  const response = await fetch(
    `${API_BASE_URL}/v1/tasks/${input.taskId}${CONTROL_SUFFIX[input.action]}`,
    {
      method: 'POST',
      headers: mutatingHeaders(input.scope),
      body: JSON.stringify(body)
    }
  );
  return parseResponse<TaskView>(response);
}
