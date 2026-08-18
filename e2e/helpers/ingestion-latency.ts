import { expect, type Locator, type Page, type Request, type Response } from '@playwright/test';
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { DashboardPage } from '../pages/dashboard.page';

export const INGESTION_RUNS_DIR = path.join(
  'evaluations', 'CHAT', 'ingestion-latency', 'runs'
);

const API_PREFIX = '/backend/v1/cowork/chat';
const GROUNDED_QUESTION =
  'According to the attached document, what is the main procedure or requirement described?';

export interface IngestionFixture {
  scenario: string;
  fixtureId: string;
  path: string;
  mediaType: string;
}

export interface IngestionEnvironment {
  timingLogPath: string;
  timingLogStartOffset: number;
  expectLocal: boolean;
  repetitions: number;
}

export interface CollectedIngestionSample {
  documentId: string | null;
  scenario: string;
  fixture_id: string;
  media_type: string;
  bytes: number;
  pages: number;
  chunks: number;
  status: string;
  retrieval_verified: boolean;
  metrics_ms: Pick<IngestionLatencySample['metrics_ms'],
    | 'hash'
    | 'initiate'
    | 'signed_put'
    | 'complete'
    | 'attach_to_server_ready'
    | 'server_ready_to_ui_ready'
    | 'attach_to_ready'
    | 'send_to_first_token'
    | 'send_to_complete'>;
}

export interface IngestionLatencySample {
  scenario: string;
  fixture_id: string;
  media_type: string;
  bytes: number;
  pages: number;
  chunks: number;
  snapshot_bytes: number | null;
  database_host_class: 'loopback' | 'remote' | null;
  storage_provider: string | null;
  embedding_provider: string | null;
  status: string;
  retrieval_verified: boolean;
  metrics_ms: {
    hash: number | null;
    initiate: number | null;
    signed_put: number | null;
    complete: number | null;
    attach_to_server_ready: number | null;
    server_ready_to_ui_ready: number | null;
    attach_to_ready: number | null;
    send_to_first_token: number | null;
    send_to_complete: number | null;
    queue_delay: number | null;
    worker_execution: number | null;
    source_download: number | null;
    extraction_chunking: number | null;
    chunk_persistence: number | null;
    embedding: number | null;
    local_index_update: number | null;
    snapshot_upload: number | null;
    ready_transition: number | null;
  };
}

export interface JoinedIngestionSamples {
  samples: IngestionLatencySample[];
  protocolErrors: string[];
}

interface ReadyDocument {
  document_id: string;
  status: string;
  page_count: number;
  chunk_count: number;
}

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required for the live ingestion latency harness.`);
  return value;
}

function positiveInteger(name: string, fallback: number): number {
  const raw = process.env[name]?.trim();
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return value;
}

export function readIngestionEnvironment(): IngestionEnvironment {
  const expectLocalRaw = process.env.CHAT_INGESTION_EXPECT_LOCAL?.trim() ?? '1';
  if (!['0', '1'].includes(expectLocalRaw)) {
    throw new Error('CHAT_INGESTION_EXPECT_LOCAL must be 0 or 1.');
  }
  const expectLocal = expectLocalRaw === '1';
  const timingLogPath = path.resolve(requiredEnv('CHAT_INGESTION_TIMING_LOG'));
  return {
    timingLogPath,
    timingLogStartOffset: existsSync(timingLogPath) ? statSync(timingLogPath).size : 0,
    expectLocal,
    repetitions: positiveInteger('CHAT_INGESTION_REPETITIONS', 10),
  };
}

export function repositoryFixtures(): IngestionFixture[] {
  return [
    {
      scenario: 'cold-position-1-small-pdf',
      fixtureId: 'dang-ky-xe-pdf-v1',
      path: path.resolve('data', 'raw', 'dang_ky_xe.pdf'),
      mediaType: 'application/pdf',
    },
    {
      scenario: 'warm-position-2-medium-pdf',
      fixtureId: 'procedure-116194-pdf-v1',
      path: path.resolve('data', 'raw', 'chi-tiet-thu-tuc-1.116194-1786096137126.pdf'),
      mediaType: 'application/pdf',
    },
    {
      scenario: 'warm-position-3-docx',
      fixtureId: 'law-31-2024-docx-v1',
      path: path.resolve('data', 'raw', '31_2024_QH15_523642.docx'),
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    },
  ];
}

export async function bootstrapGuestDashboard(page: Page): Promise<void> {
  await page.goto('/#dashboard');
  await page.waitForFunction(
    () => Boolean(window.localStorage.getItem('v-assistant-active-project-id')),
    undefined,
    { timeout: 30_000 },
  );
  const health = await page.evaluate(async (prefix) => {
    const response = await fetch(`${prefix}/document-health`, { credentials: 'include' });
    const payload = await response.json() as {
      status?: string;
      checks?: Record<string, string>;
    };
    return { httpStatus: response.status, payload };
  }, API_PREFIX);
  if (health.payload.checks?.feature !== 'enabled') {
    throw new Error('Live preflight failed: user documents are not enabled.');
  }
  if (health.httpStatus !== 200 || health.payload.status !== 'ready') {
    throw new Error(
      `Live preflight failed: document plane is ${health.payload.status ?? health.httpStatus}.`
    );
  }
}

export async function createLatencyProject(page: Page, repetition: number): Promise<string> {
  const projectId = await page.evaluate(async ({ prefix, name }) => {
    const response = await fetch(`${prefix}/projects`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!response.ok) throw new Error(`Could not create latency project (HTTP ${response.status}).`);
    const payload = await response.json() as { project_id?: string };
    if (!payload.project_id) throw new Error('Project creation did not return project_id.');
    window.localStorage.setItem('v-assistant-active-project-id', payload.project_id);
    return payload.project_id;
  }, {
    prefix: API_PREFIX,
    name: `Ingestion latency ${Date.now()} ${repetition + 1}`,
  });
  await page.reload();
  await page.waitForFunction(
    (expected) => window.localStorage.getItem('v-assistant-active-project-id') === expected,
    projectId,
    { timeout: 30_000 },
  );
  return projectId;
}

export async function deleteLatencyProject(page: Page, projectId: string): Promise<void> {
  await page.evaluate(async ({ prefix, id }) => {
    const response = await fetch(`${prefix}/projects/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'include',
    });
    if (!response.ok && response.status !== 404) {
      throw new Error(`Could not delete latency project (HTTP ${response.status}).`);
    }
  }, { prefix: API_PREFIX, id: projectId });
}

function documentCollectionPath(projectId: string): string {
  return `${API_PREFIX}/projects/${encodeURIComponent(projectId)}/documents`;
}

function isCollectionRequest(request: Request, projectId: string): boolean {
  return request.method() === 'POST' && new URL(request.url()).pathname.endsWith(
    documentCollectionPath(projectId)
  );
}

function isCompleteRequest(request: Request, projectId: string): boolean {
  const pathname = new URL(request.url()).pathname;
  return request.method() === 'POST'
    && pathname.includes(`${documentCollectionPath(projectId)}/`)
    && pathname.endsWith('/complete');
}

async function responseDuration(response: Response): Promise<number> {
  await response.finished();
  const responseEnd = response.request().timing().responseEnd;
  return Number.isFinite(responseEnd) && responseEnd >= 0 ? Math.round(responseEnd) : 0;
}

async function pollServerTerminalDocument(
  page: Page,
  projectId: string,
  documentId: string,
): Promise<{ document: ReadyDocument; observedAt: number }> {
  const deadline = Date.now() + 5 * 60_000;
  const documentPath = `${documentCollectionPath(projectId)}/${encodeURIComponent(documentId)}`;
  const documentUrl = new URL(documentPath, page.url()).toString();
  while (Date.now() < deadline) {
    const response = await page.request.get(documentUrl, { failOnStatusCode: false });
    if (response.ok()) {
      const document = await response.json() as ReadyDocument;
      if (['ready', 'failed'].includes(document.status)) {
        return { document, observedAt: Date.now() };
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Document ${documentId} did not reach a terminal status within 5 minutes.`);
}

async function installAttachmentStatusObserver(attachment: Locator) {
  await attachment.evaluate((element) => {
    type TimingWindow = Window & {
      __ingestionAttachmentReadyAt?: number;
      __ingestionAttachmentObserver?: MutationObserver;
    };
    const timingWindow = window as TimingWindow;
    timingWindow.__ingestionAttachmentReadyAt = undefined;
    timingWindow.__ingestionAttachmentObserver?.disconnect();
    const inspect = () => {
      const status = element.getAttribute('data-attachment-status');
      if (status === 'ready' || status === 'error') {
        timingWindow.__ingestionAttachmentReadyAt = Date.now();
        timingWindow.__ingestionAttachmentObserver?.disconnect();
      }
    };
    const observer = new MutationObserver(inspect);
    timingWindow.__ingestionAttachmentObserver = observer;
    observer.observe(element, { attributes: true, attributeFilter: ['data-attachment-status'] });
    inspect();
  });
}

async function attachmentReadyAt(page: Page): Promise<number> {
  await page.waitForFunction(() => typeof (
    window as Window & { __ingestionAttachmentReadyAt?: number }
  ).__ingestionAttachmentReadyAt === 'number', undefined, { timeout: 5 * 60_000 });
  return page.evaluate(() => (
    window as unknown as Window & { __ingestionAttachmentReadyAt: number }
  ).__ingestionAttachmentReadyAt);
}

async function installFirstTokenObserver(page: Page): Promise<void> {
  await page.evaluate(() => {
    type TimingWindow = Window & {
      __ingestionFirstTokenAt?: number;
      __ingestionFirstTokenObserver?: MutationObserver;
    };
    const timingWindow = window as TimingWindow;
    timingWindow.__ingestionFirstTokenAt = undefined;
    timingWindow.__ingestionFirstTokenObserver?.disconnect();
    const baseline = document.querySelectorAll('[data-testid="assistant-message-content"]').length;
    const inspect = () => {
      const assistants = document.querySelectorAll('[data-testid="assistant-message-content"]');
      if (assistants.length <= baseline) return;
      const latest = assistants.item(assistants.length - 1);
      if (latest.textContent?.trim()) {
        timingWindow.__ingestionFirstTokenAt = Date.now();
        timingWindow.__ingestionFirstTokenObserver?.disconnect();
      }
    };
    const observer = new MutationObserver(inspect);
    timingWindow.__ingestionFirstTokenObserver = observer;
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });
}

async function firstTokenAt(page: Page): Promise<number> {
  await page.waitForFunction(() => {
    return typeof (window as Window & { __ingestionFirstTokenAt?: number })
      .__ingestionFirstTokenAt === 'number';
  }, undefined, { timeout: 120_000 });
  return page.evaluate(() => (
    window as unknown as Window & { __ingestionFirstTokenAt: number }
  ).__ingestionFirstTokenAt);
}

function sseReferencesDocument(body: string, documentId: string): boolean {
  for (const block of body.split('\n\n')) {
    const data = block.split('\n').find((line) => line.startsWith('data:'));
    if (!data) continue;
    try {
      const event = JSON.parse(data.slice(5).trim()) as {
        event_type?: string;
        document_id?: string;
        rag_evidence?: Array<{
          source?: string;
          retrieval_status?: string;
          document_id?: string;
        }>;
      };
      if (event.event_type === 'memory_citation' && event.document_id === documentId) return true;
      if (event.rag_evidence?.some((item) => (
        item.source === 'project_document'
        && item.retrieval_status === 'success'
        && item.document_id === documentId
      ))) return true;
    } catch {
      // Malformed/non-JSON SSE blocks cannot provide retrieval verification.
    }
  }
  return false;
}

async function measureIngestionCaseUnsafe(
  page: Page,
  dashboard: DashboardPage,
  projectId: string,
  fixture: IngestionFixture,
  progress: {
    documentId: string | null;
    readyObservation: CollectedIngestionSample | null;
  },
): Promise<CollectedIngestionSample> {
  await expect(dashboard.composer).toBeVisible({ timeout: 30_000 });
  await dashboard.composer.fill(GROUNDED_QUESTION);

  let initiateStartedAt: number | null = null;
  const initiateRequestPromise = page.waitForRequest((request) => {
    if (!isCollectionRequest(request, projectId)) return false;
    initiateStartedAt ??= Date.now();
    return true;
  });
  const initiateResponsePromise = page.waitForResponse((response) => (
    isCollectionRequest(response.request(), projectId)
  ));
  const putResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'PUT'
  ));
  const completeResponsePromise = page.waitForResponse((response) => (
    isCompleteRequest(response.request(), projectId)
  ));
  const attachAt = Date.now();

  await dashboard.fileInput.setInputFiles(fixture.path);
  const attachment = dashboard.attachment(path.basename(fixture.path));
  await expect(attachment).toBeVisible();
  await expect(dashboard.sendButton).toBeDisabled();
  await installAttachmentStatusObserver(attachment);

  await initiateRequestPromise;
  const initiateResponse = await initiateResponsePromise;
  const initiated = await initiateResponse.json() as {
    document_id?: string;
    upload_url?: string;
  };
  progress.documentId = initiated.document_id ?? null;
  if (!initiated.document_id || !initiated.upload_url) {
    throw new Error('Fresh latency project did not initiate a signed document upload.');
  }

  const terminalPromise = pollServerTerminalDocument(page, projectId, initiated.document_id);
  const [putResponse, completeResponse, terminal] = await Promise.all([
    putResponsePromise,
    completeResponsePromise,
    terminalPromise,
  ]);
  await expect(attachment).toHaveAttribute(
    'data-attachment-status',
    terminal.document.status === 'ready' ? 'ready' : 'error',
    {
      timeout: 5 * 60_000,
    },
  );
  const uiReadyAt = await attachmentReadyAt(page);
  expect(await attachment.getAttribute('data-document-id')).toBe(terminal.document.document_id);
  const reachedReady = terminal.document.status === 'ready';
  const uploadMetrics = {
    hash: initiateStartedAt === null ? null : Math.max(0, initiateStartedAt - attachAt),
    initiate: await responseDuration(initiateResponse),
    signed_put: await responseDuration(putResponse),
    complete: await responseDuration(completeResponse),
    attach_to_server_ready: reachedReady ? Math.max(0, terminal.observedAt - attachAt) : null,
    server_ready_to_ui_ready: reachedReady ? Math.max(0, uiReadyAt - terminal.observedAt) : null,
    attach_to_ready: reachedReady ? Math.max(0, uiReadyAt - attachAt) : null,
  };
  const metadata = {
    documentId: terminal.document.document_id,
    scenario: fixture.scenario,
    fixture_id: fixture.fixtureId,
    media_type: fixture.mediaType,
    bytes: statSync(fixture.path).size,
    pages: terminal.document.page_count,
    chunks: terminal.document.chunk_count,
    status: terminal.document.status,
  };
  if (!reachedReady) {
    return {
      ...metadata,
      retrieval_verified: false,
      metrics_ms: {
        ...uploadMetrics,
        send_to_first_token: null,
        send_to_complete: null,
      },
    };
  }
  progress.readyObservation = {
    ...metadata,
    retrieval_verified: false,
    metrics_ms: {
      ...uploadMetrics,
      send_to_first_token: null,
      send_to_complete: null,
    },
  };
  await expect(dashboard.sendButton).toBeEnabled();
  await dashboard.closeProjectDocuments();

  await installFirstTokenObserver(page);
  const chatResponsePromise = page.waitForResponse((response) => {
    const request = response.request();
    return request.method() === 'POST'
      && /\/sessions\/[^/]+\/messages$/.test(new URL(response.url()).pathname);
  });
  const sendAt = Date.now();
  await dashboard.sendButton.click();
  const chatResponse = await chatResponsePromise;
  const tokenAtPromise = firstTokenAt(page);
  const sseBody = await chatResponse.text();
  const sendCompleteAt = Date.now();
  const tokenAt = await tokenAtPromise;
  const retrievalVerified = sseReferencesDocument(sseBody, terminal.document.document_id);

  return {
    ...metadata,
    retrieval_verified: retrievalVerified,
    metrics_ms: {
      ...uploadMetrics,
      send_to_first_token: retrievalVerified ? Math.max(0, tokenAt - sendAt) : null,
      send_to_complete: retrievalVerified ? Math.max(0, sendCompleteAt - sendAt) : null,
    },
  };
}

export async function measureIngestionCase(
  page: Page,
  dashboard: DashboardPage,
  projectId: string,
  fixture: IngestionFixture,
): Promise<CollectedIngestionSample> {
  const progress = {
    documentId: null as string | null,
    readyObservation: null as CollectedIngestionSample | null,
  };
  try {
    return await measureIngestionCaseUnsafe(page, dashboard, projectId, fixture, progress);
  } catch {
    return progress.readyObservation ?? failedIngestionCase(fixture, progress.documentId);
  }
}

export function failedIngestionCase(
  fixture: IngestionFixture,
  documentId: string | null = null,
): CollectedIngestionSample {
  return {
    documentId,
    scenario: fixture.scenario,
    fixture_id: fixture.fixtureId,
    media_type: fixture.mediaType,
    bytes: statSync(fixture.path).size,
    pages: 0,
    chunks: 0,
    status: 'failed',
    retrieval_verified: false,
    metrics_ms: {
      hash: null,
      initiate: null,
      signed_put: null,
      complete: null,
      attach_to_server_ready: null,
      server_ready_to_ui_ready: null,
      attach_to_ready: null,
      send_to_first_token: null,
      send_to_complete: null,
    },
  };
}

const BACKEND_STAGES = [
  'queue_delay',
  'worker_execution',
  'source_download',
  'extraction_chunking',
  'chunk_persistence',
  'embedding',
  'local_index_update',
  'snapshot_upload',
  'ready_transition',
] as const;

type BackendStage = typeof BACKEND_STAGES[number];

interface BackendTimingEvent {
  documentId: string;
  stage: BackendStage;
  durationMs: number;
  outcome: 'success' | 'error';
  databaseHostClass?: 'loopback' | 'remote';
  snapshotBytes?: number;
  provider?: string;
}

function parseBackendTimingLog(logPath: string, startOffset: number): {
  events: BackendTimingEvent[];
  errors: string[];
} {
  const events: BackendTimingEvent[] = [];
  const errors: string[] = [];
  const stageSet = new Set<string>(BACKEND_STAGES);
  const timestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
  const providerPattern = /^[a-z][a-z0-9_]*$/;
  let contents: Buffer;
  try {
    contents = readFileSync(logPath);
  } catch {
    return { events, errors: ['ingestion timing log is missing or unreadable'] };
  }
  const currentRun = contents.subarray(Math.min(startOffset, contents.length)).toString('utf8');
  const lines = currentRun.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) continue;
    try {
      const raw = JSON.parse(line) as Record<string, unknown>;
      if (
        raw.schema_version !== 1
        || typeof raw.timestamp !== 'string'
        || !timestampPattern.test(raw.timestamp)
        || !Number.isFinite(Date.parse(raw.timestamp))
        || typeof raw.document_id !== 'string'
        || !raw.document_id
        || typeof raw.stage !== 'string'
        || !stageSet.has(raw.stage)
        || !Number.isSafeInteger(raw.duration_ms)
        || (raw.duration_ms as number) < 0
        || !['success', 'error'].includes(raw.outcome as string)
      ) {
        throw new Error('required field mismatch');
      }
      if (
        raw.database_host_class !== undefined
        && !['loopback', 'remote'].includes(raw.database_host_class as string)
      ) throw new Error('invalid database_host_class');
      if (
        raw.snapshot_bytes !== undefined
        && (!Number.isSafeInteger(raw.snapshot_bytes) || (raw.snapshot_bytes as number) < 0)
      ) throw new Error('invalid snapshot_bytes');
      if (
        raw.provider !== undefined
        && (typeof raw.provider !== 'string' || !providerPattern.test(raw.provider))
      ) throw new Error('invalid provider');
      events.push({
        documentId: raw.document_id,
        stage: raw.stage as BackendStage,
        durationMs: raw.duration_ms as number,
        outcome: raw.outcome as 'success' | 'error',
        databaseHostClass: raw.database_host_class as 'loopback' | 'remote' | undefined,
        snapshotBytes: raw.snapshot_bytes as number | undefined,
        provider: raw.provider as string | undefined,
      });
    } catch {
      errors.push(`timing log line ${index + 1} violates the ingestion event contract`);
    }
  }
  return { events, errors };
}

export function joinBackendTimings(
  collected: CollectedIngestionSample[],
  environment: IngestionEnvironment,
): JoinedIngestionSamples {
  const parsed = parseBackendTimingLog(
    environment.timingLogPath, environment.timingLogStartOffset
  );
  const byDocument = new Map<string, Map<BackendStage, BackendTimingEvent>>();
  const duplicateStages = new Map<string, Set<BackendStage>>();
  for (const event of parsed.events) {
    const stages = byDocument.get(event.documentId) ?? new Map();
    if (stages.has(event.stage)) {
      const duplicates = duplicateStages.get(event.documentId) ?? new Set();
      duplicates.add(event.stage);
      duplicateStages.set(event.documentId, duplicates);
    } else stages.set(event.stage, event);
    byDocument.set(event.documentId, stages);
  }
  const queueEvents = parsed.events.filter((event) => (
    event.stage === 'queue_delay' && event.databaseHostClass !== undefined
  ));
  const storageEvents = parsed.events.filter((event) => (
    event.stage === 'source_download' && event.provider !== undefined
  ));
  const embeddingEvents = parsed.events.filter((event) => (
    event.stage === 'embedding' && event.provider !== undefined
  ));
  const runDatabaseHostClass = queueEvents.at(-1)?.databaseHostClass;
  const runStorageProvider = storageEvents.at(-1)?.provider;
  const runEmbeddingProvider = embeddingEvents.at(-1)?.provider;
  const protocolErrors = [...parsed.errors];
  if (!runDatabaseHostClass || !runStorageProvider || !runEmbeddingProvider) {
    protocolErrors.push('timing log lacks actual database/storage/embedding metadata');
  }
  const databaseHostClasses = new Set(queueEvents.map((event) => event.databaseHostClass));
  if (databaseHostClasses.size > 1) {
    protocolErrors.push('backend timing log mixes loopback and remote database hosts');
  }
  const storageProviders = new Set(storageEvents.map((event) => event.provider));
  const embeddingProviders = new Set(embeddingEvents.map((event) => event.provider));
  if (storageProviders.size > 1) {
    protocolErrors.push('backend timing log mixes storage providers');
  }
  if (embeddingProviders.size > 1) {
    protocolErrors.push('backend timing log mixes embedding providers');
  }
  if (environment.expectLocal && runDatabaseHostClass !== 'loopback') {
    protocolErrors.push('expected local ingestion run but backend reported remote database host');
  }
  const samples = collected.map((observation) => {
    const events = observation.documentId
      ? byDocument.get(observation.documentId) ?? new Map<BackendStage, BackendTimingEvent>()
      : new Map<BackendStage, BackendTimingEvent>();
    const duplicates = observation.documentId
      ? duplicateStages.get(observation.documentId) ?? new Set<BackendStage>()
      : new Set<BackendStage>();
    const eventFor = (stage: BackendStage) => duplicates.has(stage) ? undefined : events.get(stage);
    const queue = eventFor('queue_delay');
    const sourceDownload = eventFor('source_download');
    const embedding = eventFor('embedding');
    const snapshot = eventFor('snapshot_upload')?.snapshotBytes
      ?? eventFor('local_index_update')?.snapshotBytes
      ?? null;
    const databaseHostClass = queue?.databaseHostClass ?? runDatabaseHostClass ?? null;
    const storageProvider = sourceDownload?.provider ?? runStorageProvider ?? null;
    const embeddingProvider = embedding?.provider ?? runEmbeddingProvider ?? null;
    const hasDuplicateStage = duplicates.size > 0;
    const backendComplete = !hasDuplicateStage && BACKEND_STAGES.every((stage) => (
      events.get(stage)?.outcome === 'success'
    )) && queue?.databaseHostClass !== undefined
      && sourceDownload?.provider !== undefined
      && embedding?.provider !== undefined;
    const status = observation.status === 'ready' && !backendComplete
      ? 'incomplete'
      : observation.status;
    if (observation.status === 'ready' && !backendComplete) {
      protocolErrors.push(`${observation.fixture_id} is missing successful backend timing stages`);
    }
    if (hasDuplicateStage) {
      protocolErrors.push(`${observation.fixture_id} has duplicate backend timing stages`);
    }
    if (environment.expectLocal && databaseHostClass !== 'loopback') {
      protocolErrors.push(`${observation.fixture_id} used a remote database in a local run`);
    }
    const backendMetrics = Object.fromEntries(BACKEND_STAGES.map((stage) => [
      stage,
      eventFor(stage)?.durationMs ?? null,
    ])) as Record<BackendStage, number | null>;
    return {
      scenario: observation.scenario,
      fixture_id: observation.fixture_id,
      media_type: observation.media_type,
      bytes: observation.bytes,
      pages: observation.pages,
      chunks: observation.chunks,
      snapshot_bytes: snapshot,
      database_host_class: databaseHostClass,
      storage_provider: storageProvider,
      embedding_provider: embeddingProvider,
      status,
      retrieval_verified: observation.retrieval_verified,
      metrics_ms: {
        ...observation.metrics_ms,
        ...backendMetrics,
      },
    } satisfies IngestionLatencySample;
  });
  return { samples, protocolErrors };
}

export function writeIngestionSamples(samples: IngestionLatencySample[]): string {
  mkdirSync(INGESTION_RUNS_DIR, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const destination = path.join(INGESTION_RUNS_DIR, `${stamp}.json`);
  writeFileSync(destination, `${JSON.stringify({ samples }, null, 2)}\n`, 'utf8');
  return destination;
}
