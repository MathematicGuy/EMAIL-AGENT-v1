import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Dashboard } from './Dashboard';

const callbacks = vi.hoisted(() => ({
  approve: undefined as ((taskId: string) => Promise<void> | void) | undefined,
  revise: undefined as
    | ((taskId: string, feedback: string) => Promise<void> | void)
    | undefined,
}));

const streaming = vi.hoisted(() => ({
  approveWorkflowPlan: vi.fn(),
  reviseWorkflowPlan: vi.fn(),
}));

vi.mock('./hooks/useStreamingChat', () => ({
  useStreamingChat: () => ({
    messages: [{ message_id: 'assistant-1' }],
    isGenerating: false,
    inputText: '',
    setInputText: vi.fn(),
    selectedAttachments: [],
    attachmentError: null,
    selectAttachments: vi.fn(),
    removeAttachment: vi.fn(),
    sendMessage: vi.fn(),
    stopGeneration: vi.fn(),
    resetChat: vi.fn(),
    loadExistingChat: vi.fn(),
    apiStatus: 'live',
    recentChats: [],
    isHistoryLoading: false,
    activeConversationId: null,
    workflows: {},
    approveWorkflowPlan: streaming.approveWorkflowPlan,
    reviseWorkflowPlan: streaming.reviseWorkflowPlan,
    retryWorkflowStep: vi.fn(),
  }),
}));

vi.mock('./components/Taskbar', () => ({ Taskbar: () => null }));
vi.mock('./components/Header', () => ({ Header: () => null }));
vi.mock('./components/HeroSection', () => ({ HeroSection: () => null }));
vi.mock('./components/AutomationsView', () => ({ AutomationsView: () => null }));
vi.mock('./components/ModelSelectorModal', () => ({ ModelSelectorModal: () => null }));
vi.mock('./components/UpgradeModal', () => ({ UpgradeModal: () => null }));
vi.mock('./components/VoiceModal', () => ({ VoiceModal: () => null }));
vi.mock('./components/CustomizeModal', () => ({ CustomizeModal: () => null }));
vi.mock('../modules/work-intake/WorkIntakePanel', () => ({ WorkIntakePanel: () => null }));
vi.mock('./components/ChatStreamView', () => ({
  ChatStreamView: (props: {
    onApproveWorkflowPlan?: (taskId: string) => Promise<void> | void;
    onReviseWorkflowPlan?: (taskId: string, feedback: string) => Promise<void> | void;
  }) => {
    callbacks.approve = props.onApproveWorkflowPlan;
    callbacks.revise = props.onReviseWorkflowPlan;
    return null;
  },
}));

describe('Dashboard plan callbacks', () => {
  it('preserves workflow plan promises for the chat card', () => {
    const approvePending = new Promise<void>(() => undefined);
    const revisePending = new Promise<void>(() => undefined);
    streaming.approveWorkflowPlan.mockReturnValue(approvePending);
    streaming.reviseWorkflowPlan.mockReturnValue(revisePending);

    render(<Dashboard />);

    expect(callbacks.approve?.('task-1')).toBe(approvePending);
    expect(callbacks.revise?.('task-1', 'Thêm kiểm tra')).toBe(revisePending);
  });
});
