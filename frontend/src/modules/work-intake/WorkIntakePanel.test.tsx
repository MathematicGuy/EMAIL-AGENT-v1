import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkIntakePanel } from './WorkIntakePanel';
import type {
  ApprovalDecision,
  OnFailure,
  PendingActionApproval,
  PlanStep,
  PlanView,
  ResourceAccess,
  StepStatus,
  StepView,
  TaskDetail,
  TaskStatus,
  TaskView
} from './types';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  });
}

function taskView(status: TaskStatus, pauseRequested = false): TaskView {
  return {
    schema_version: '1.0',
    task_id: 'task-1',
    run_id: 'run-1',
    request_id: 'req-1',
    client_request_id: 'creq-1',
    status,
    state_version: 3,
    pause_requested: pauseRequested,
    cancellation_generation: 0,
    current_plan_version: 1,
    actor_id: 'demo-user',
    project_id: 'demo-project',
    workspace_id: 'demo-workspace',
    created_at: '2026-07-25T08:00:00Z',
    updated_at: '2026-07-25T08:01:00Z'
  };
}

function stepView(
  stepId: string,
  state: StepStatus,
  dependsOn: string[] = []
): StepView {
  return {
    step_id: stepId,
    task_type: 'extract',
    assigned_module: 'module_2',
    capability_id: 'extract.table',
    capability_version: '1.0.0',
    state,
    required: true,
    allow_partial: false,
    accepted_partial: false,
    current_attempt: 1,
    current_job_id: null,
    operation_revision: 1,
    depends_on: dependsOn.map((id) => ({
      step_id: id,
      required: true,
      accepted_states: ['SUCCEEDED' as StepStatus]
    })),
    completion_criteria: [],
    updated_at: '2026-07-25T08:01:00Z'
  };
}

function planStep(
  stepId: string,
  dependsOn: string[] = []
): PlanStep {
  return {
    step_id: stepId,
    task_type: 'extract',
    assigned_module: 'module_2',
    capability_id: 'extract.table',
    capability_version: '1.0.0',
    required: true,
    allow_partial: false,
    depends_on: dependsOn.map((id) => ({
      step_id: id,
      required: true,
      accepted_states: ['SUCCEEDED' as StepStatus]
    })),
    input_refs: [],
    context_requirement: {
      scope_type: 'workspace',
      scope_id: 'demo-workspace',
      required_memory_types: [],
      token_budget: 8000
    },
    expected_output_schema: {},
    completion_criteria: [
      {
        criterion_id: 'crit-1',
        rule: 'return status=success',
        mandatory: true
      }
    ],
    timeout_seconds: 300,
    retry_policy: {
      max_retries: 2,
      retryable_categories: ['TRANSIENT', 'TIMEOUT', 'DEPENDENCY'],
      backoff_seconds: 1.0
    },
    resource_access: 'SNAPSHOT_READ' as ResourceAccess,
    mutation: 'NONE',
    approval_required: false,
    on_failure: 'FAIL_TASK' as OnFailure,
    operation_revision: 1,
    operation_key: `op-${stepId}`,
    idempotency_key: `idem-${stepId}`
  };
}

function planView(
  steps: StepView[],
  planSteps: PlanStep[] = [],
  approved = false
): PlanView {
  return {
    plan_id: 'plan-1',
    plan_version: 1,
    objective: 'Objective: tổng hợp doanh thu quý 2 theo chi nhánh',
    scope: ['Workspace finance'],
    out_of_scope: ['Dữ liệu ngoài workspace'],
    constraints: [],
    assumptions: ['Nguồn dữ liệu đã được đồng bộ'],
    risks: [],
    completion_criteria: [],
    approval_mode: 'REQUIRE_INTERACTIVE',
    approved,
    steps,
    plan_steps: planSteps,
    query_template_hash: 'hash-abc'
  };
}

function taskDetail(task: TaskView, steps: StepView[]): TaskDetail {
  return {
    task,
    run: {
      run_id: 'run-1',
      task_id: 'task-1',
      created_at: '2026-07-25T08:00:00Z',
      active_budget_used_ms: 1200,
      absolute_deadline: '2026-07-25T08:30:00Z',
      replan_count: 0,
      context_degraded: false,
      memory_degraded: false,
      checkpoint: {}
    },
    plan: planView(steps),
    steps
  };
}

function pendingAction(): PendingActionApproval {
  return {
    action_id: 'action-1',
    step_id: 'step_calendar_event',
    capability_id: 'calendar.create_event',
    payload_hash: `sha256:${'a'.repeat(64)}`,
    scope: 'demo-workspace/demo-project',
    expires_at: '2099-01-01T00:00:00+00:00',
    state_version: 3,
    preview: {
      action_id: 'action-1',
      capability_id: 'calendar.create_event',
      payload_hash: `sha256:${'a'.repeat(64)}`,
      account_label: 'Demo F-Cowork',
      calendar_id: 'team@f-cowork.local',
      expires_at: '2099-01-01T00:00:00+00:00',
      summary: 'Hội nghị AI Việt Nam 2026',
      all_day: false,
      location: null,
      start: {
        date_time: '2026-08-15T09:00:00',
        date: null,
        time_zone: 'Asia/Ho_Chi_Minh'
      },
      end: {
        date_time: '2026-08-15T10:00:00',
        date: null,
        time_zone: 'Asia/Ho_Chi_Minh'
      },
      source_citations: [
        {
          field: 'summary',
          result_index: 0,
          url: 'https://example.com/ai-2026',
          assumed: false
        },
        {
          field: 'start',
          result_index: 0,
          url: 'https://example.com/ai-2026',
          assumed: false
        },
        {
          field: 'end',
          result_index: 0,
          url: 'https://example.com/ai-2026',
          assumed: true
        }
      ]
    }
  };
}

function stubFetch(
  handler: (url: string, method: string) => Response
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
      handler(String(input), (init?.method ?? 'GET').toUpperCase())
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function submitQueryText(text = 'Tổng hợp doanh thu quý 2'): void {
  fireEvent.change(screen.getByLabelText('Yêu cầu công việc'), {
    target: { value: text }
  });
  fireEvent.click(screen.getByText('Submit request'));
}

describe('WorkIntakePanel', () => {
  it('does not render when closed', () => {
    render(<WorkIntakePanel isOpen={false} onClose={() => undefined} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('submits a query and renders the interpretation, plan and step statuses', async () => {
    const steps = [
      stepView('step-extract', 'SUCCEEDED'),
      stepView('step-report', 'RUNNING', ['step-extract'])
    ];
    stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('RUNNING'));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse(taskDetail(taskView('RUNNING'), steps));
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    expect(await screen.findByText('Query interpretation')).toBeTruthy();
    expect(
      screen.getByText('Objective: tổng hợp doanh thu quý 2 theo chi nhánh')
    ).toBeTruthy();
    expect(screen.getByText('Workspace finance')).toBeTruthy();
    expect(screen.getByText('Nguồn dữ liệu đã được đồng bộ')).toBeTruthy();

    expect(screen.getByText(/step-report/)).toBeTruthy();
    expect(screen.getAllByText(/module_2 · extract.table@/).length).toBe(2);
    expect(screen.getByText(/depends on: step-extract/)).toBeTruthy();

    expect(screen.getByText('SUCCEEDED')).toBeTruthy();
    expect(screen.getAllByText('RUNNING').length).toBeGreaterThan(0);
    expect(screen.getByText(/Run progress 50%/)).toBeTruthy();
  });

  it('hides the approval control when the task is not awaiting approval', async () => {
    const steps = [stepView('step-extract', 'RUNNING')];
    stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('RUNNING'));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse(taskDetail(taskView('RUNNING'), steps));
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    expect(await screen.findByText(/step-extract/)).toBeTruthy();
    expect(screen.queryByText('Approve plan')).toBeNull();
  });

  it('shows the approval control while awaiting approval and calls the approve endpoint', async () => {
    const steps = [stepView('step-extract', 'PENDING')];
    const fetchMock = stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('AWAITING_PLAN_APPROVAL'));
      }
      if (method === 'POST' && url.endsWith('/plan:approve')) {
        return jsonResponse(planView(steps, [], true));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse(
          taskDetail(taskView('AWAITING_PLAN_APPROVAL'), steps)
        );
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    const approveButton = await screen.findByText('Approve plan');
    fireEvent.click(approveButton);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith('/v1/tasks/task-1/plan:approve')
        )
      ).toBe(true)
    );
  });

  it('calls the pause endpoint when pause is clicked', async () => {
    const steps = [stepView('step-extract', 'RUNNING')];
    const fetchMock = stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('RUNNING'));
      }
      if (method === 'POST' && url.endsWith(':pause')) {
        return jsonResponse(taskView('RUNNING', true));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse(taskDetail(taskView('RUNNING'), steps));
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    await screen.findByText(/step-extract/);
    fireEvent.click(screen.getByText('Pause'));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith('/v1/tasks/task-1:pause')
        )
      ).toBe(true)
    );
  });

  it('renders user_message and error_code from an ErrorBody response', async () => {
    stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(
          {
            error_code: 'QUERY_SCHEMA_INVALID',
            category: 'VALIDATION',
            retryable: false,
            user_message: 'Yêu cầu không hợp lệ, vui lòng mô tả rõ hơn.',
            origin_module: 'module_1',
            failed_requirement: 'query',
            details: {}
          },
          422
        );
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    expect(
      await screen.findByText('Yêu cầu không hợp lệ, vui lòng mô tả rõ hơn.', {
        exact: false
      })
    ).toBeTruthy();
    expect(screen.getByText('QUERY_SCHEMA_INVALID')).toBeTruthy();
  });

  it('renders the final result with degraded flags for a terminal task', async () => {
    const steps = [stepView('step-extract', 'SUCCEEDED')];
    const terminalDetail: TaskDetail = {
      ...taskDetail(taskView('COMPLETED'), steps),
      final_response: {
        schema_version: '1.0',
        task_id: 'task-1',
        run_id: 'run-1',
        terminal_status: 'COMPLETED',
        summary: 'Đã tổng hợp doanh thu quý 2.',
        findings: [
          {
            finding_id: 'f-1',
            importance: 'IMPORTANT',
            statement: 'Doanh thu tăng 12% so với quý 1.',
            evidence_refs: ['ev-1']
          }
        ],
        artifact_refs: [{ ref_id: 'artifact-report-1', checksum: 'sha256:aa' }],
        artifact_grounding: [
          {
            artifact_ref_id: 'artifact-report-1',
            grounding: {
              status: 'GROUNDED',
              label: 'Grounded Result',
              source_coverage: 1,
              sources: [
                {
                  marker: 1,
                  kind: 'DOCUMENT',
                  reference_id: 'ev-1',
                  display_name: 'sheet-q2',
                  source_ref_id: 'ref-1',
                  locator: { cell: 'B12' }
                }
              ]
            }
          }
        ],
        resource_refs: [{ ref_id: 'resource-sheet-1', checksum: 'sha256:bb' }],
        evidence_refs: [
          {
            evidence_id: 'ev-1',
            source_ref_id: 'ref-1',
            source_id: 'sheet-q2',
            source_version: '3',
            checksum: 'sha256:cc',
            locator: { cell: 'B12' },
            accessed_at: '2026-07-25T08:02:00Z',
            excerpt: 'Q2 total 1.2B'
          }
        ],
        assumptions: [],
        unresolved: ['Thiếu số liệu tháng 6 của chi nhánh 3'],
        context_degraded: true,
        memory_degraded: true
      }
    };
    stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('RUNNING'));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse(terminalDetail);
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    expect(await screen.findByText('Final result')).toBeTruthy();
    expect(screen.getByText('Đã tổng hợp doanh thu quý 2.')).toBeTruthy();
    expect(screen.getByText(/Doanh thu tăng 12%/)).toBeTruthy();
    expect(screen.getByText('artifact-report-1')).toBeTruthy();
    expect(screen.getByText(/Grounded Result/)).toBeTruthy();
    expect(screen.getByText(/\[1\] sheet-q2/)).toBeTruthy();
    expect(screen.getByText('resource-sheet-1')).toBeTruthy();
    expect(screen.getByText(/Q2 total 1.2B/)).toBeTruthy();
    expect(screen.getByText('Thiếu số liệu tháng 6 của chi nhánh 3')).toBeTruthy();
    expect(screen.getByText('context_degraded')).toBeTruthy();
    expect(screen.getByText('memory_degraded')).toBeTruthy();
  });

  it('allows editing plan steps and submits the patch with full step objects', async () => {
    const steps = [stepView('step-extract', 'PENDING')];
    const pSteps = [planStep('step-extract')];
    const fetchMock = stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('AWAITING_PLAN_APPROVAL'));
      }
      if (method === 'PATCH' && url.endsWith('/plan')) {
        // Capture the request body
        return jsonResponse(planView(steps, pSteps, true));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        const detail = taskDetail(taskView('AWAITING_PLAN_APPROVAL'), steps);
        return jsonResponse({
          ...detail,
          plan: planView(steps, pSteps)
        });
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    // Wait for the revision form to be visible
    expect(await screen.findByText('Revise plan')).toBeTruthy();
    fireEvent.click(screen.getByText('Revise plan'));

    // Find and edit the timeout field - use getByDisplayValue to find the input
    const timeoutInput = screen.getByDisplayValue('300') as HTMLInputElement;
    fireEvent.change(timeoutInput, { target: { value: '600' } });

    // Submit the patch
    fireEvent.click(screen.getByText('Submit plan patch'));

    // Verify the PATCH call was made with full step objects
    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([input]) => String(input).includes('/v1/tasks/task-1/plan')
      );
      expect(patchCall).toBeTruthy();

      // Extract the body from the PATCH call
      const [, init] = patchCall!;
      const body = JSON.parse(init!.body as string) as {
        steps: PlanStep[];
      };

      // Verify the step has the full PlanStep structure with the edit applied
      const patchedStep = body.steps[0];
      expect(patchedStep.timeout_seconds).toBe(600);
      expect(patchedStep.step_id).toBe('step-extract');
      expect(patchedStep.capability_id).toBe('extract.table');
      expect(patchedStep.operation_key).toBe('op-step-extract');
      expect(patchedStep.idempotency_key).toBe('idem-step-extract');
      expect(patchedStep.required).toBe(true);
    });
  });

  it('renders the action preview with the source of every field', async () => {
    const steps = [stepView('step_calendar_event', 'WAITING_APPROVAL')];
    stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('AWAITING_ACTION_APPROVAL'));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse({
          ...taskDetail(taskView('AWAITING_ACTION_APPROVAL'), steps),
          action_approval: pendingAction()
        });
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    expect(await screen.findByText('Action approval required')).toBeTruthy();
    expect(screen.getByText('Hội nghị AI Việt Nam 2026')).toBeTruthy();
    expect(
      screen.getByText('2026-08-15T09:00:00 (Asia/Ho_Chi_Minh)')
    ).toBeTruthy();
    // The label and the calendar actually written to are both shown, because
    // nothing in the system compares them.
    expect(screen.getByText('Demo F-Cowork')).toBeTruthy();
    expect(screen.getByText('team@f-cowork.local')).toBeTruthy();
    // The derived end edge must be marked, not passed off as sourced.
    expect(screen.getByText(/Giả định/)).toBeTruthy();
    // summary, start and end each name the result they were read from.
    expect(screen.getAllByText(/example.com\/ai-2026/).length).toBe(3);
    expect(screen.getByText('Không có nguồn')).toBeTruthy();
  });

  it('sends every binding field back when the action is approved', async () => {
    const steps = [stepView('step_calendar_event', 'WAITING_APPROVAL')];
    const fetchMock = stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('AWAITING_ACTION_APPROVAL'));
      }
      if (method === 'POST' && url.includes(':approve')) {
        return jsonResponse(taskView('RUNNING'));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse({
          ...taskDetail(taskView('AWAITING_ACTION_APPROVAL'), steps),
          action_approval: pendingAction()
        });
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    fireEvent.click(await screen.findByText('Approve action'));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input]) =>
        String(input).endsWith('/v1/tasks/task-1/actions/action-1:approve')
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(call![1]!.body as string) as ApprovalDecision;
      expect(body.decision).toBe('approved');
      expect(body.action_id).toBe('action-1');
      // Copied from the server's pending approval, never recomputed here.
      expect(body.payload_hash).toBe(`sha256:${'a'.repeat(64)}`);
      expect(body.scope).toBe('demo-workspace/demo-project');
      expect(body.state_version).toBe(3);
      expect(body.expires_at).toBe('2099-01-01T00:00:00+00:00');
    });
  });

  it('denies the action through the same endpoint', async () => {
    const steps = [stepView('step_calendar_event', 'WAITING_APPROVAL')];
    const fetchMock = stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('AWAITING_ACTION_APPROVAL'));
      }
      if (method === 'POST' && url.includes(':approve')) {
        return jsonResponse(taskView('CANCELLED'));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse({
          ...taskDetail(taskView('AWAITING_ACTION_APPROVAL'), steps),
          action_approval: pendingAction()
        });
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    fireEvent.click(await screen.findByText('Deny'));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input]) =>
        String(input).includes(':approve')
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(call![1]!.body as string) as ApprovalDecision;
      expect(body.decision).toBe('denied');
    });
  });

  it('shows no action card when nothing is parked', async () => {
    const steps = [stepView('step-extract', 'RUNNING')];
    stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('RUNNING'));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse(taskDetail(taskView('RUNNING'), steps));
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText();

    await screen.findByText(/step-extract/);
    expect(screen.queryByText('Action approval required')).toBeNull();
  });

  it('handles raw web search query by executing step_search without action approval card', async () => {
    const searchStepView = stepView('step_search', 'SUCCEEDED');
    const searchPlanStep = planStep('step_search');
    const detail: TaskDetail = {
      ...taskDetail(taskView('COMPLETED'), [searchStepView]),
      plan: planView([searchStepView], [searchPlanStep], true),
      final_response: {
        schema_version: '1.0',
        task_id: 'task-1',
        run_id: 'run-1',
        terminal_status: 'COMPLETED',
        context_degraded: false,
        memory_degraded: false,
        summary: 'Giá điện sinh hoạt năm 2026 giữ nguyên theo quyết định mới nhất.',
        findings: [],
        artifact_grounding: [],
        artifact_refs: [],
        resource_refs: [],
        evidence_refs: [],
        assumptions: [],
        unresolved: []
      },
      action_approval: null
    };

    stubFetch((url, method) => {
      if (method === 'POST' && url.endsWith('/v1/tasks')) {
        return jsonResponse(taskView('RUNNING'));
      }
      if (method === 'GET' && url.includes('/v1/tasks/task-1')) {
        return jsonResponse(detail);
      }
      throw new Error(`unexpected ${method} ${url}`);
    });

    render(<WorkIntakePanel isOpen onClose={() => undefined} />);
    submitQueryText('Tra cứu tin tức mới nhất về giá điện trên mạng');

    await screen.findByText(/step_search/);
    expect(screen.getByText(/Giá điện sinh hoạt năm 2026/)).toBeTruthy();
    expect(screen.queryByText('Action approval required')).toBeNull();
  });
});



