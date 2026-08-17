import type { Page, Route } from '@playwright/test';

export const DEFAULT_PROJECT_ID = 'project-latency';
export const CHAT_A_ID = 'session-latency-a';
export const CHAT_B_ID = 'session-latency-b';
export const CHAT_HEAVY_ID = 'session-latency-heavy';

export const CHAT_A_TITLE = 'Latency Chat A';
export const CHAT_B_TITLE = 'Latency Chat B';
export const CHAT_HEAVY_TITLE = 'Latency Chat Heavy';

export const CHAT_A_MARKER = 'LATENCY-MARKER-A';
export const CHAT_B_MARKER = 'LATENCY-MARKER-B';
export const CHAT_HEAVY_MARKER = 'LATENCY-MARKER-HEAVY';

export interface MockTurn {
  turn_id: string;
  user_message: string;
  assistant_message: string;
  created_at: string;
  citation_coordinates: Array<Record<string, unknown>>;
  rag_evidence: Array<Record<string, unknown>>;
  retrieval_status: string | null;
  mail_scan: Record<string, unknown> | null;
}

export interface ChatApiMockOptions {
  delayMsBySession?: Record<string, number>;
  extraTurnsForHeavy?: number;
  evidencePerHeavyTurn?: number;
  evidenceContentChars?: number;
}

export interface ChatApiMockHandle {
  messagesFetchCount: () => number;
}

function ragEvidence(index: number, contentChars: number): Record<string, unknown> {
  return {
    source: 'company_knowledge',
    retrieval_status: 'success',
    chunk_id: `chunk-${index}`,
    document_id: `doc-${index}`,
    document_title: `Policy ${index}.md`,
    section: 'Overview',
    source_url: null,
    relevance_score: 0.81,
    rerank_score: 0.77,
    preview: `Preview ${index}`.padEnd(80, '.'),
    content: `CONTENT-${index}-`.padEnd(contentChars, 'x'),
  };
}

function lightTurns(marker: string): MockTurn[] {
  return [
    {
      turn_id: `turn-${marker}-1`,
      user_message: `Question ${marker}`,
      assistant_message: `Answer ${marker}`,
      created_at: '2026-08-17T00:00:00Z',
      citation_coordinates: [],
      rag_evidence: [],
      retrieval_status: 'no_results',
      mail_scan: null,
    },
  ];
}

function heavyTurns(
  marker: string,
  turnCount: number,
  evidencePerTurn: number,
  contentChars: number,
): MockTurn[] {
  return Array.from({ length: turnCount }, (_, turnIndex) => ({
    turn_id: `turn-${marker}-${turnIndex + 1}`,
    user_message: turnIndex === 0 ? `Question ${marker}` : `Follow-up ${turnIndex} ${marker}`,
    assistant_message: turnIndex === 0 ? `Answer ${marker}` : `Reply ${turnIndex} ${marker}`,
    created_at: `2026-08-17T00:${String(turnIndex).padStart(2, '0')}:00Z`,
    citation_coordinates: [
      {
        citation_scope: 'project_document',
        project_id: DEFAULT_PROJECT_ID,
        document_id: 'doc-1',
        document_title: 'Policy.md',
        page_start: 1,
        page_end: 2,
      },
    ],
    rag_evidence: Array.from({ length: evidencePerTurn }, (_, evidenceIndex) =>
      ragEvidence(turnIndex * evidencePerTurn + evidenceIndex, contentChars),
    ),
    retrieval_status: 'success',
    mail_scan: null,
  }));
}

export function buildSessionPayload(
  sessionId: string,
  options: ChatApiMockOptions = {},
): { turns: MockTurn[] } {
  if (sessionId === CHAT_HEAVY_ID) {
    return {
      turns: heavyTurns(
        CHAT_HEAVY_MARKER,
        options.extraTurnsForHeavy ?? 16,
        options.evidencePerHeavyTurn ?? 5,
        options.evidenceContentChars ?? 4_000,
      ),
    };
  }
  if (sessionId === CHAT_B_ID) {
    return { turns: lightTurns(CHAT_B_MARKER) };
  }
  return { turns: lightTurns(CHAT_A_MARKER) };
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

export async function installChatApiMocks(
  page: Page,
  options: ChatApiMockOptions = {},
): Promise<ChatApiMockHandle> {
  let messagesFetchCount = 0;

  // Registered first so later, more specific handlers take precedence.
  await page.route('**/backend/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/v1/cowork/chat/')) {
      await route.fallback();
      return;
    }
    if (url.includes('/health')) {
      await json(route, { status: 'ok' });
      return;
    }
    await json(route, { detail: 'unmocked' }, 404);
  });

  await page.route('**/v1/cowork/chat/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/backend/, '');
    const method = request.method();

    if (path.endsWith('/v1/cowork/chat/guest-session') && method === 'POST') {
      await route.fulfill({ status: 204, body: '' });
      return;
    }
    if (path.endsWith('/v1/cowork/chat/projects') && method === 'GET') {
      await json(route, {
        projects: [
          {
            project_id: DEFAULT_PROJECT_ID,
            name: 'Latency Project',
            is_default: true,
            created_at: '2026-08-17T00:00:00Z',
          },
        ],
      });
      return;
    }
    if (path.endsWith('/v1/cowork/chat/document-health') && method === 'GET') {
      await json(route, { status: 'ready', checks: { feature: 'enabled' } });
      return;
    }
    if (path.endsWith('/v1/cowork/chat/sessions') && method === 'GET') {
      await json(route, {
        sessions: [
          { session_id: CHAT_A_ID, project_id: DEFAULT_PROJECT_ID, title: CHAT_A_TITLE },
          { session_id: CHAT_B_ID, project_id: DEFAULT_PROJECT_ID, title: CHAT_B_TITLE },
          { session_id: CHAT_HEAVY_ID, project_id: DEFAULT_PROJECT_ID, title: CHAT_HEAVY_TITLE },
        ],
      });
      return;
    }
    const messagesMatch = path.match(/\/v1\/cowork\/chat\/sessions\/([^/]+)\/messages$/);
    if (messagesMatch && method === 'GET') {
      messagesFetchCount += 1;
      const sessionId = decodeURIComponent(messagesMatch[1]);
      const delayMs = options.delayMsBySession?.[sessionId] ?? 0;
      if (delayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, delayMs));
      }
      await json(route, { session_id: sessionId, ...buildSessionPayload(sessionId, options) });
      return;
    }
    if (path.endsWith('/health') || path.endsWith('/api/v1/health')) {
      await json(route, { status: 'ok' });
      return;
    }
    await json(route, { detail: `Unmocked chat route: ${method} ${path}` }, 404);
  });

  await page.route('**/api/v1/health', async (route) => {
    await json(route, { status: 'ok' });
  });

  return {
    messagesFetchCount: () => messagesFetchCount,
  };
}
