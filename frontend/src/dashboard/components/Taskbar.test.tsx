import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Taskbar } from './Taskbar';

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
});
