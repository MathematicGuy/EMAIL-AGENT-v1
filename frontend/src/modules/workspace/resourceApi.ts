import { API_BASE_URL } from '../../lib/apiConfig';
import type { SourceSnapshotRef } from '../work-intake/types';

export interface ResourceScope {
  actorId: string;
  projectId: string;
  workspaceId: string;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      user_message?: string;
      error_code?: string;
      detail?: { user_message?: string; error_code?: string };
    };
    return (
      body.user_message ??
      body.error_code ??
      body.detail?.user_message ??
      body.detail?.error_code ??
      `${response.status} ${response.statusText}`
    );
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

export async function uploadResource(
  file: File,
  scope: ResourceScope,
  signal?: AbortSignal
): Promise<SourceSnapshotRef> {
  const body = new FormData();
  body.append('file', file);
  body.append('project_id', scope.projectId);
  body.append('workspace_id', scope.workspaceId);
  const response = await fetch(`${API_BASE_URL}/v1/resources/uploads`, {
    method: 'POST',
    headers: { 'X-Actor-Id': scope.actorId },
    body,
    signal
  });
  if (!response.ok) {
    throw new Error(`Upload thất bại: ${await errorMessage(response)}`);
  }
  return response.json();
}

export async function readResourceText(
  refId: string,
  scope: ResourceScope
): Promise<string> {
  const params = new URLSearchParams({
    project_id: scope.projectId,
    workspace_id: scope.workspaceId
  });
  const response = await fetch(
    `${API_BASE_URL}/v1/resources/${encodeURIComponent(refId)}?${params}`,
    { headers: { 'X-Actor-Id': scope.actorId } }
  );
  if (!response.ok) {
    throw new Error(`Không đọc được artifact: ${await errorMessage(response)}`);
  }
  return response.text();
}
