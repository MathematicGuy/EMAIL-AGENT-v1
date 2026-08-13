import { API_BASE_URL } from '../../lib/apiConfig';

export type ProjectDocumentStatus =
  | 'received' | 'extracting' | 'indexing' | 'ready' | 'failed' | 'deleted';

export interface ProjectDocument {
  document_id: string;
  project_id: string;
  title: string;
  media_type: string;
  size_bytes: number;
  status: ProjectDocumentStatus;
  reason_code: string | null;
  page_count: number;
  chunk_count: number;
  ocr_page_count: number;
  expires_at: string;
}

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  let detail = `HTTP ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: string };
    detail = payload.detail ?? detail;
  } catch { /* keep status */ }
  throw new Error(detail);
}

export async function listProjectDocuments(projectId: string): Promise<ProjectDocument[]> {
  const response = await checked(await fetch(
    `${API_BASE_URL}/v1/cowork/chat/projects/${encodeURIComponent(projectId)}/documents`
  ));
  return ((await response.json()) as { documents: ProjectDocument[] }).documents;
}

export async function uploadProjectDocument(
  projectId: string,
  file: File
): Promise<ProjectDocument> {
  const form = new FormData();
  form.append('file', file);
  const response = await checked(await fetch(
    `${API_BASE_URL}/v1/cowork/chat/projects/${encodeURIComponent(projectId)}/documents`,
    { method: 'POST', body: form }
  ));
  return (await response.json()) as ProjectDocument;
}

export async function deleteProjectDocument(
  projectId: string,
  documentId: string
): Promise<void> {
  await checked(await fetch(
    `${API_BASE_URL}/v1/cowork/chat/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}`,
    { method: 'DELETE' }
  ));
}
