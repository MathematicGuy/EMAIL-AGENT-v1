import type {
  ApiErrorShape,
  ContextSnapshot,
  MemoryClaim,
  MemoryClaimListResponse,
  MemoryClaimSourcesResponse,
  MemoryContextPreview,
  MemoryListResponse,
  MemoryRecord,
  ProjectMemorySummary
} from './types';
import { API_BASE_URL } from '../../lib/apiConfig';

export class MemoryApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(
    message: string,
    code: string,
    status: number
  ) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T | ApiErrorShape;
  if (!response.ok) {
    const error = payload as ApiErrorShape;
    throw new MemoryApiError(
      error.user_message ?? 'Memory service request failed.',
      error.error_code ?? 'MEMORY_REQUEST_FAILED',
      response.status
    );
  }
  return payload as T;
}

export async function listMemories(input: {
  actorId: string;
  projectId: string;
  workspaceId: string;
  query?: string;
}): Promise<MemoryListResponse> {
  const params = new URLSearchParams({
    project_id: input.projectId,
    workspace_id: input.workspaceId
  });
  if (input.query) params.set('query', input.query);
  const response = await fetch(`${API_BASE_URL}/v1/memories?${params}`, {
    headers: { 'X-Actor-Id': input.actorId }
  });
  return parseResponse<MemoryListResponse>(response);
}

export async function updateMemory(input: {
  memory: MemoryRecord;
  content: string;
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<MemoryRecord> {
  const params = new URLSearchParams({
    project_id: input.projectId,
    workspace_id: input.workspaceId
  });
  const response = await fetch(
    `${API_BASE_URL}/v1/memories/${input.memory.memory_id}?${params}`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'X-Actor-Id': input.actorId,
        'If-Match': String(input.memory.version)
      },
      body: JSON.stringify({ content: input.content })
    }
  );
  return parseResponse<MemoryRecord>(response);
}

export async function deleteMemory(input: {
  memory: MemoryRecord;
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<MemoryRecord> {
  const params = new URLSearchParams({
    project_id: input.projectId,
    workspace_id: input.workspaceId
  });
  const response = await fetch(
    `${API_BASE_URL}/v1/memories/${input.memory.memory_id}?${params}`,
    {
      method: 'DELETE',
      headers: {
        'X-Actor-Id': input.actorId,
        'If-Match': String(input.memory.version)
      }
    }
  );
  return parseResponse<MemoryRecord>(response);
}

export async function resolveContextSnapshot(input: {
  snapshotId: string;
  accessToken: string;
  serviceIdentity: string;
  jobId: string;
  attempt: number;
  operationRevision: number;
}): Promise<ContextSnapshot> {
  const params = new URLSearchParams({
    access_token: input.accessToken,
    job_id: input.jobId,
    attempt: String(input.attempt),
    operation_revision: String(input.operationRevision)
  });
  const response = await fetch(
    `${API_BASE_URL}/internal/v1/context-snapshots/${input.snapshotId}?${params}`,
    { headers: { 'X-Service-Identity': input.serviceIdentity } }
  );
  return parseResponse<ContextSnapshot>(response);
}

function scopeParams(input: {
  projectId: string;
  workspaceId: string;
}): URLSearchParams {
  return new URLSearchParams({
    project_id: input.projectId,
    workspace_id: input.workspaceId
  });
}

export async function getProjectMemorySummary(input: {
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<ProjectMemorySummary> {
  const response = await fetch(
    `${API_BASE_URL}/v1/memory-summary?${scopeParams(input)}`,
    { headers: { 'X-Actor-Id': input.actorId } }
  );
  return parseResponse<ProjectMemorySummary>(response);
}

export async function refreshProjectMemorySummary(input: {
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<ProjectMemorySummary> {
  const response = await fetch(
    `${API_BASE_URL}/v1/memory-summary:refresh?${scopeParams(input)}`,
    {
      method: 'POST',
      headers: { 'X-Actor-Id': input.actorId }
    }
  );
  return parseResponse<ProjectMemorySummary>(response);
}

export async function listMemoryClaims(input: {
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<MemoryClaimListResponse> {
  const response = await fetch(
    `${API_BASE_URL}/v1/memory-claims?${scopeParams(input)}`,
    { headers: { 'X-Actor-Id': input.actorId } }
  );
  return parseResponse<MemoryClaimListResponse>(response);
}

export async function confirmMemoryClaim(input: {
  claim: MemoryClaim;
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<MemoryClaim> {
  const response = await fetch(
    `${API_BASE_URL}/v1/memory-claims/${input.claim.claim_id}:confirm?${scopeParams(input)}`,
    {
      method: 'POST',
      headers: { 'X-Actor-Id': input.actorId }
    }
  );
  return parseResponse<MemoryClaim>(response);
}

export async function correctMemoryClaim(input: {
  claim: MemoryClaim;
  content: string;
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<MemoryClaim> {
  const response = await fetch(
    `${API_BASE_URL}/v1/memory-claims/${input.claim.claim_id}?${scopeParams(input)}`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'X-Actor-Id': input.actorId,
        'If-Match': String(input.claim.version)
      },
      body: JSON.stringify({ content: input.content })
    }
  );
  return parseResponse<MemoryClaim>(response);
}

export async function forgetMemoryClaim(input: {
  claim: MemoryClaim;
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<MemoryClaim> {
  const response = await fetch(
    `${API_BASE_URL}/v1/memory-claims/${input.claim.claim_id}?${scopeParams(input)}`,
    {
      method: 'DELETE',
      headers: {
        'X-Actor-Id': input.actorId,
        'If-Match': String(input.claim.version)
      }
    }
  );
  return parseResponse<MemoryClaim>(response);
}

export async function getMemoryClaimSources(input: {
  claimId: string;
  actorId: string;
  projectId: string;
  workspaceId: string;
}): Promise<MemoryClaimSourcesResponse> {
  const response = await fetch(
    `${API_BASE_URL}/v1/memory-claims/${input.claimId}/sources?${scopeParams(input)}`,
    { headers: { 'X-Actor-Id': input.actorId } }
  );
  return parseResponse<MemoryClaimSourcesResponse>(response);
}

export async function previewMemoryContext(input: {
  actorId: string;
  projectId: string;
  workspaceId: string;
  query: string;
  objective?: string;
  topK?: number;
  tokenBudget?: number;
}): Promise<MemoryContextPreview> {
  const response = await fetch(`${API_BASE_URL}/v1/memory-context-previews`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Actor-Id': input.actorId
    },
    body: JSON.stringify({
      project_id: input.projectId,
      workspace_id: input.workspaceId,
      query: input.query,
      objective: input.objective ?? '',
      top_k: input.topK ?? 10,
      token_budget: input.tokenBudget ?? 8000
    })
  });
  return parseResponse<MemoryContextPreview>(response);
}
