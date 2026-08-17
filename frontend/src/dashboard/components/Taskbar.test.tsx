import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Taskbar } from './Taskbar';
import type { RecentChat } from '../types';

const baseProps = {
  onToggleSidebar: vi.fn(),
  onNewChat: vi.fn(),
  onNewChatInProject: vi.fn(),
  onCreateProject: vi.fn(),
  projects: [],
  activeProjectId: '',
  onSelectProject: vi.fn(),
  onSelectRecent: vi.fn(),
  onDeleteChat: vi.fn(),
  recentChats: [],
  onNavigateHome: vi.fn(),
  onChangeView: vi.fn(),
};

describe('Taskbar', () => {
  it('omits retired navigation controls from the expanded sidebar', () => {
    render(<Taskbar {...baseProps} sidebarState="expanded" />);

    expect(screen.queryByText('Dispatch')).toBeNull();
    expect(screen.queryByText('Customize')).toBeNull();
    expect(screen.queryByText('Home')).toBeNull();
    expect(screen.queryByText('Code')).toBeNull();
    expect(screen.queryByText('Design')).toBeNull();
    expect(screen.queryByText('Labs')).toBeNull();
    expect(screen.queryAllByTitle('Download app')).toHaveLength(0);
  });

  it('omits retired icon controls from the collapsed sidebar', () => {
    render(<Taskbar {...baseProps} sidebarState="collapsed" />);

    expect(screen.queryByTitle('Dispatch Operations')).toBeNull();
    expect(screen.queryByTitle('Customize')).toBeNull();
    expect(screen.queryByTitle('Projects')).toBeNull();
    expect(screen.queryByTitle('Code & Plan Upgrade')).toBeNull();
    expect(screen.queryAllByTitle('Download app')).toHaveLength(0);
  });

  it('shows an inactive generating chat and prevents deleting it', () => {
    const generatingChat = {
      id: 'chat-generating',
      title: 'Drafting launch plan',
      generationStatus: 'generating',
    } as RecentChat;

    render(
      <Taskbar
        {...baseProps}
        sidebarState="expanded"
        activeChatId="another-chat"
        recentChats={[generatingChat]}
      />,
    );

    expect(screen.getByText('Generating')).toBeTruthy();
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Delete Drafting launch plan' }).disabled,
    ).toBe(true);
  });

  it.each([
    ['failed', 'Failed'],
    ['interrupted', 'Interrupted'],
    ['cancelled', 'Cancelled'],
    ['usage_limit_reached', 'Usage limit reached'],
    ['temporarily_rate_limited', 'Temporarily rate-limited'],
  ] as const)('shows a textual indicator for the %s lifecycle', (generationStatus, label) => {
    const chat = {
      id: `chat-${generationStatus}`,
      title: 'Recoverable chat',
      generationStatus,
    } as RecentChat;

    render(<Taskbar {...baseProps} sidebarState="expanded" recentChats={[chat]} />);

    expect(screen.getByText(label)).toBeTruthy();
  });

  it('shows an unread completion dot only while the completed chat is inactive', () => {
    const completedChat = {
      id: 'chat-completed',
      title: 'Completed in background',
      generationStatus: 'completed',
      unread: true,
    } as RecentChat;

    const { rerender } = render(
      <Taskbar
        {...baseProps}
        sidebarState="expanded"
        activeChatId="another-chat"
        recentChats={[completedChat]}
      />,
    );

    expect(screen.getByLabelText('Unread')).toBeTruthy();

    rerender(
      <Taskbar
        {...baseProps}
        sidebarState="expanded"
        activeChatId="chat-completed"
        recentChats={[completedChat]}
      />,
    );

    expect(screen.queryByLabelText('Unread')).toBeNull();
  });
});
