import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_BASE_URL } from '../../lib/apiConfig';
import { listTaskEvents, retryStep, revisePlan, WorkIntakeApiError } from './api';
import type { WorkIntakeScope } from './api';

const SCOPE: WorkIntakeScope = {
  actorId: 'demo-user',
  projectId: 'demo-project',
  workspaceId: 'demo-workspace',
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('work intake task workflow clients', () => {
  it('reads task events from a cursor', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ events: [], next_sequence: 7, has_more: false }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      listTaskEvents({ taskId: 'task-1', afterSequence: 7, scope: SCOPE })
    ).resolves.toMatchObject({ next_sequence: 7 });

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `${API_BASE_URL}/v1/tasks/task-1/events?after_sequence=7`
    );
    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({
      'X-Actor-Id': 'demo-user',
    });
  });

  it('posts a plan revision with the expected version', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ plan_version: 2, approved: false }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      revisePlan({
        taskId: 'task-1',
        expectedPlanVersion: 1,
        feedback: 'Bỏ bước thông báo.',
        scope: SCOPE,
      })
    ).resolves.toMatchObject({ plan_version: 2 });

    expect(String(fetchMock.mock.calls[0][0])).toContain('/v1/tasks/task-1/plan:revise');
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(body).toMatchObject({
      schema_version: '1.0',
      expected_plan_version: 1,
      feedback: 'Bỏ bước thông báo.',
    });
    expect(body.command_id).toMatch(/^cmd_/);
  });

  it('posts a step retry with both expected versions', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ task: { task_id: 'task-1' }, steps: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await retryStep({
      taskId: 'task-1',
      stepId: 'step_synthesize',
      expectedPlanVersion: 1,
      expectedStateVersion: 4,
      scope: SCOPE,
    });

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/v1/tasks/task-1/steps/step_synthesize:retry'
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toMatchObject({
      expected_plan_version: 1,
      expected_state_version: 4,
    });
  });

  it('surfaces the server error code when a retry is rejected', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error_code: 'STATE_VERSION_CONFLICT',
            user_message: 'This failure is not retryable.',
          },
          409
        )
      )
    );

    await expect(
      retryStep({
        taskId: 'task-1',
        stepId: 'step_synthesize',
        expectedPlanVersion: 1,
        expectedStateVersion: 4,
        scope: SCOPE,
      })
    ).rejects.toMatchObject({
      code: 'STATE_VERSION_CONFLICT',
      status: 409,
    });
    expect(WorkIntakeApiError).toBeDefined();
  });
});
