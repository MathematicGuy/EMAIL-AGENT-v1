import { expect, test } from '@playwright/test';
import {
  bootstrapGuestDashboard,
  createLatencyProject,
  deleteLatencyProject,
  failedIngestionCase,
  joinBackendTimings,
  measureIngestionCase,
  readIngestionEnvironment,
  repositoryFixtures,
  writeIngestionSamples,
  type CollectedIngestionSample,
  type IngestionEnvironment,
  type JoinedIngestionSamples,
} from './helpers/ingestion-latency';
import { DashboardPage } from './pages/dashboard.page';

const liveEnabled = process.env.CHAT_INGESTION_LATENCY_LIVE === '1';
const collected: CollectedIngestionSample[] = [];
let environment: IngestionEnvironment | null = null;
let joined: JoinedIngestionSamples | null = null;

test.describe('project document ingestion UX latency @live', () => {
  test.skip(
    !liveEnabled,
    'Set CHAT_INGESTION_LATENCY_LIVE=1 and CHAT_INGESTION_TIMING_LOG.',
  );
  test.describe.configure({ mode: 'serial' });

  test.afterAll(() => {
    if (collected.length === 0 || environment === null) return;
    joined ??= joinBackendTimings(collected, environment);
    writeIngestionSamples(joined.samples);
  });

  test('measures cold and warm sequential uploads with retrieval verification', async ({ page }) => {
    environment = readIngestionEnvironment();
    test.setTimeout(environment.repetitions * 3 * 8 * 60_000);
    await bootstrapGuestDashboard(page);
    const dashboard = new DashboardPage(page);
    const collectionErrors: string[] = [];
    const fixtures = repositoryFixtures();
    let stopFurtherRepetitions = false;

    for (let repetition = 0; repetition < environment.repetitions; repetition += 1) {
      let projectId: string | null = null;
      try {
        projectId = await createLatencyProject(page, repetition);
      } catch {
        collectionErrors.push(`repetition ${repetition + 1}: project creation failed`);
        collected.push(...fixtures.map((fixture) => failedIngestionCase(fixture)));
        continue;
      }
      try {
        for (const fixture of fixtures) {
          try {
            const observation = await measureIngestionCase(page, dashboard, projectId, fixture);
            collected.push(observation);
            if (observation.status !== 'ready') {
              collectionErrors.push(`repetition ${repetition + 1}: ${fixture.fixtureId} failed`);
              await page.reload().catch(() => undefined);
            }
            const incremental = joinBackendTimings(collected, environment);
            if (
              environment.expectLocal
              && incremental.samples.at(-1)?.database_host_class === 'remote'
            ) {
              stopFurtherRepetitions = true;
              break;
            }
          } catch {
            collectionErrors.push(`repetition ${repetition + 1}: ${fixture.fixtureId} failed`);
            collected.push(failedIngestionCase(fixture));
            await page.reload().catch(() => undefined);
          }
        }
      } finally {
        await deleteLatencyProject(page, projectId).catch(() => {
          collectionErrors.push(`repetition ${repetition + 1}: project cleanup failed`);
        });
      }
      if (stopFurtherRepetitions) break;
    }

    joined = joinBackendTimings(collected, environment);
    const incomplete = joined.samples.filter((sample) => (
      sample.status !== 'ready'
      || sample.pages === 0
      || sample.chunks === 0
      || !sample.retrieval_verified
      || Object.values(sample.metrics_ms).some((metric) => metric === null)
    ));
    expect(collected).toHaveLength(environment.repetitions * fixtures.length);
    expect([...collectionErrors, ...joined.protocolErrors]).toEqual([]);
    expect(incomplete).toEqual([]);
  });
});
