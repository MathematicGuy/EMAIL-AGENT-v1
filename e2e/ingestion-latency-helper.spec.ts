import { expect, test } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import {
  joinBackendTimings,
  type CollectedIngestionSample,
  type IngestionEnvironment,
} from './helpers/ingestion-latency';

const stages = [
  'queue_delay', 'worker_execution', 'source_download', 'extraction_chunking',
  'chunk_persistence', 'embedding', 'local_index_update', 'snapshot_upload',
  'ready_transition',
] as const;

function observation(documentId = 'document-1'): CollectedIngestionSample {
  return {
    documentId,
    scenario: 'cold-position-1-small-pdf',
    fixture_id: 'fixture-1',
    media_type: 'application/pdf',
    bytes: 100,
    pages: 1,
    chunks: 1,
    status: 'ready',
    retrieval_verified: true,
    metrics_ms: {
      hash: 1, initiate: 1, signed_put: 1, complete: 1,
      attach_to_server_ready: 1, server_ready_to_ui_ready: 1, attach_to_ready: 2,
      send_to_first_token: 1, send_to_complete: 2,
    },
  };
}

function environment(timingLogPath: string): IngestionEnvironment {
  return {
    timingLogPath,
    timingLogStartOffset: 0,
    expectLocal: true,
    repetitions: 10,
  };
}

function events(documentId: string, storage = 'local_storage', embedding = 'gemini') {
  return stages.map((stage) => ({
    schema_version: 1,
    timestamp: '2026-08-18T01:02:03.004Z',
    document_id: documentId,
    stage,
    duration_ms: 1,
    outcome: 'success',
    ...(stage === 'queue_delay' ? { database_host_class: 'loopback' } : {}),
    ...(stage === 'source_download' ? { provider: storage } : {}),
    ...(stage === 'embedding' ? { provider: embedding } : {}),
  }));
}

test('unusable timing logs produce nullable incomplete metadata instead of throwing', ({}, testInfo) => {
  const joined = joinBackendTimings(
    [observation()], environment(testInfo.outputPath('missing.jsonl'))
  );

  expect(joined.samples[0].status).toBe('incomplete');
  expect(joined.samples[0].metrics_ms.queue_delay).toBeNull();
  expect(joined.samples[0].database_host_class).toBeNull();
  expect(joined.samples[0].storage_provider).toBeNull();
  expect(joined.samples[0].embedding_provider).toBeNull();
  expect(joined.protocolErrors).not.toEqual([]);
});

test('duplicate stages and mixed providers make affected samples non-baselineable', ({}, testInfo) => {
  const logPath = testInfo.outputPath('timings.jsonl');
  const rows = [
    ...events('document-1', 'local_storage', 'gemini'),
    events('document-1')[0],
    ...events('document-2', 'supabase_storage', 'jina'),
  ];
  writeFileSync(logPath, `${rows.map((row) => JSON.stringify(row)).join('\n')}\n`, 'utf8');

  const joined = joinBackendTimings(
    [observation('document-1'), observation('document-2')], environment(logPath)
  );

  expect(joined.samples[0].status).toBe('incomplete');
  expect(joined.samples[0].metrics_ms.queue_delay).toBeNull();
  expect(joined.protocolErrors.some((error) => error.includes('duplicate'))).toBe(true);
  expect(joined.protocolErrors.some((error) => error.includes('storage providers'))).toBe(true);
  expect(joined.protocolErrors.some((error) => error.includes('embedding providers'))).toBe(true);
});
