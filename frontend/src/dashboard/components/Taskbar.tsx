import React, { useState } from 'react';
import {
  Plus,
  MessageSquare,
  GitBranch,
  Search,
  Mail,
  LoaderCircle,
  Trash2,
  BookOpen
} from 'lucide-react';
import { ChevronDown, ChevronRight, Folder } from 'lucide-react';
import type { ChatGenerationStatus, SidebarState, RecentChat, ActiveDashboardView } from '../types';
import type { Project } from '../types/projectTypes';

const terminalLifecycleLabels: Partial<Record<ChatGenerationStatus, string>> = {
  failed: 'Failed',
  interrupted: 'Interrupted',
  cancelled: 'Cancelled',
  usage_limit_reached: 'Usage limit reached',
  temporarily_rate_limited: 'Temporarily rate-limited',
};

const ChatLifecycleIndicator = ({
  chat,
  isActive,
  legacyGenerating,
}: {
  chat: RecentChat;
  isActive: boolean;
  legacyGenerating: boolean;
}) => {
  const isChatGenerating = chat.generationStatus === 'generating' || (!chat.generationStatus && legacyGenerating);
  const terminalLabel = chat.generationStatus
    ? terminalLifecycleLabels[chat.generationStatus]
    : undefined;

  if (isChatGenerating) {
    return (
      <span className="flex shrink-0 items-center" title="Generating">
        <LoaderCircle aria-hidden="true" className="h-3 w-3 animate-spin text-[#d97757]" />
        <span className="sr-only">Generating</span>
      </span>
    );
  }

  if (terminalLabel) {
    return (
      <span
        className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-zinc-600 text-[9px] font-bold leading-none text-zinc-400"
        title={terminalLabel}
      >
        <span aria-hidden="true">!</span>
        <span className="sr-only">{terminalLabel}</span>
      </span>
    );
  }

  if (chat.unread && !isActive) {
    return (
      <span
        aria-label="Unread"
        className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#d97757] ring-2 ring-[#1b1a18]"
        title="Unread"
      />
    );
  }

  return <MessageSquare aria-hidden="true" className="h-3 w-3 shrink-0 text-[#d97757]" />;
};

interface TaskbarProps {
  sidebarState: SidebarState;
  onToggleSidebar: () => void;
  onNewChat: () => void;
  onNewChatInProject: (projectId: string) => void;
  onCreateProject: () => void;
  projects: Project[];
  activeProjectId: string;
  onSelectProject: (projectId: string) => void;
  onDeleteProject?: (project: Project) => void;
  onSelectRecent: (chat: RecentChat) => void;
  onPrefetchChat?: (chat: RecentChat) => void;
  onDeleteChat: (chat: RecentChat) => void;
  recentChats: RecentChat[];
  isHistoryLoading?: boolean;
  activeChatId?: string;
  onNavigateHome?: () => void;
  activeView?: ActiveDashboardView;
  onChangeView?: (view: ActiveDashboardView) => void;
  isGenerating?: boolean;
  initialExpandedProjectIds?: string[];
}

const SidebarToggleIcon = ({ className = "w-4 h-4" }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
    <line x1="9" y1="3" x2="9" y2="21" />
  </svg>
);

export const Taskbar: React.FC<TaskbarProps> = ({
  sidebarState,
  onToggleSidebar,
  onNewChat,
  onNewChatInProject,
  onCreateProject,
  projects,
  activeProjectId,
  onSelectProject,
  onDeleteProject,
  onSelectRecent,
  onPrefetchChat,
  onDeleteChat,
  recentChats,
  isHistoryLoading = false,
  activeChatId,
  onNavigateHome,
  activeView = 'chat',
  onChangeView,
  isGenerating = false,
  initialExpandedProjectIds,
}) => {
  void isHistoryLoading;
  void onNewChatInProject;
  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(
    () => new Set(initialExpandedProjectIds ?? [])
  );
  const isExpanded = sidebarState === 'expanded';

  if (!isExpanded) {
    return (
      <aside className="w-[52px] h-screen bg-[#1c1b18] border-r border-[#2d2b27] flex flex-col justify-between items-center py-3 select-none z-30 transition-all duration-200 ease-in-out">
        <div className="flex flex-col items-center gap-3 w-full">
          <button
            onClick={onToggleSidebar}
            title="Hiện thanh bên"
            className="w-8 h-8 flex items-center justify-center text-zinc-400 hover:text-zinc-100 hover:bg-[#2c2a26] rounded-md transition-colors cursor-pointer"
          >
            <SidebarToggleIcon className="w-4 h-4" />
          </button>

          <button
            onClick={onNavigateHome}
            className="w-9 h-9 rounded-xl overflow-hidden flex items-center justify-center bg-[#282623] border border-zinc-700/50 shadow-md hover:scale-105 transition-transform cursor-pointer"
            title="Về trang chủ"
          >
            <img src="/images/f-cowork-icon.svg" alt="F-Cowork Icon" className="w-7 h-7 object-contain" />
          </button>

          <div className="w-6 h-[1px] bg-zinc-800/80 my-0.5" />

          <button
            onClick={onNewChat}
            title="Đoạn chat mới"
            className="w-8 h-8 flex items-center justify-center text-zinc-400 hover:text-zinc-100 hover:bg-[#2c2a26] rounded-md transition-colors cursor-pointer"
          >
            <Plus className="w-4.5 h-4.5" />
          </button>

          <button
            title="Đoạn chat"
            onClick={() => onChangeView?.('chat')}
            className={`w-8 h-8 flex items-center justify-center rounded-md transition-colors cursor-pointer ${
              activeView === 'chat' ? 'bg-[#2c2a26] text-white' : 'text-zinc-400 hover:text-zinc-100 hover:bg-[#2c2a26]'
            }`}
          >
            <MessageSquare className="w-4 h-4" />
          </button>

          <button
            title="Hộp thư"
            onClick={() => onChangeView?.('mail')}
            className={`w-8 h-8 flex items-center justify-center rounded-md transition-colors cursor-pointer ${
              activeView === 'mail' ? 'bg-[#2c2a26] text-[#d97757]' : 'text-zinc-400 hover:text-zinc-100 hover:bg-[#2c2a26]'
            }`}
          >
            <Mail className="w-4 h-4" />
          </button>

          <button
            title="Artifacts"
            onClick={() => onChangeView?.('artifacts')}
            className={`w-8 h-8 flex items-center justify-center rounded-md transition-colors cursor-pointer ${
              activeView === 'artifacts'
                ? 'bg-[#2c2a26] text-[#d97757]'
                : 'text-zinc-400 hover:text-zinc-100 hover:bg-[#2c2a26]'
            }`}
          >
            <GitBranch className="w-4 h-4" />
          </button>

          <button
            title="Tài liệu quy trình"
            onClick={() => onChangeView?.('raw-documents')}
            className={`w-8 h-8 flex items-center justify-center rounded-md transition-colors cursor-pointer ${
              activeView === 'raw-documents'
                ? 'bg-[#2c2a26] text-[#d97757]'
                : 'text-zinc-400 hover:text-zinc-100 hover:bg-[#2c2a26]'
            }`}
          >
            <BookOpen className="w-4 h-4" />
          </button>
        </div>

        <div className="flex flex-col items-center gap-3 w-full">
          <button
            title="steven (Gói Pro)"
            className="w-8 h-8 rounded-full bg-[#35332f] hover:bg-[#423f3a] text-zinc-200 text-xs font-semibold flex items-center justify-center border border-zinc-700/40 transition-colors cursor-pointer"
          >
            s
          </button>
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-[260px] h-screen bg-[#1b1a18] border-r border-[#2b2926] flex flex-col justify-between px-3 py-3 select-none z-30 transition-all duration-200 ease-in-out">
      <div className="flex flex-col gap-2.5 overflow-hidden flex-1">
        <div className="flex items-center justify-between px-1 pt-1 pb-1">
          <button
            onClick={onNavigateHome}
            className="flex items-center hover:opacity-80 transition-opacity cursor-pointer"
            title="Về trang chủ"
          >
            <img
              src="/images/f-cowork-logo-no-tagline.svg"
              alt="F-Cowork Logo"
              className="h-10 sm:h-11 object-contain max-w-[180px] drop-shadow-sm"
            />
          </button>

          <div className="flex items-center gap-1 text-zinc-400">
            <button
              onClick={onToggleSidebar}
              title="Thu gọn thanh bên"
              className="p-1 hover:text-zinc-100 hover:bg-[#2c2a26] rounded-md transition-colors cursor-pointer"
            >
              <SidebarToggleIcon className="w-4 h-4" />
            </button>
            <button
              title="Tìm kiếm đoạn chat"
              className="p-1 hover:text-zinc-100 hover:bg-[#2c2a26] rounded-md transition-colors cursor-pointer"
            >
              <Search className="w-4 h-4" />
            </button>
          </div>
        </div>

        <button
          onClick={onNewChat}
          className="flex items-center gap-2 px-3 py-1.5 bg-[#2b2926] hover:bg-[#34322e] text-zinc-200 hover:text-white rounded-xl text-xs font-semibold transition-colors border border-zinc-700/40 cursor-pointer shadow-sm"
        >
          <Plus className="w-4 h-4 text-zinc-400" />
          <span>Tạo cuộc trò chuyện mới</span>
        </button>

        <div className="flex flex-col gap-0.5 text-xs">
          <button
            onClick={() => onChangeView?.('artifacts')}
            className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left transition-colors font-medium cursor-pointer ${
              activeView === 'artifacts'
                ? 'bg-[#272522] text-[#d97757] font-semibold'
                : 'text-zinc-300 hover:text-white hover:bg-[#272522]'
            }`}
          >
            <GitBranch className={`w-3.5 h-3.5 ${activeView === 'artifacts' ? 'text-[#d97757]' : 'text-zinc-400'}`} />
            <span>Artifacts</span>
          </button>

          <button
            title="Hộp thư"
            onClick={() => onChangeView?.('mail')}
            className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left transition-colors font-medium cursor-pointer ${
              activeView === 'mail' ? 'bg-[#272522] text-[#d97757] font-semibold' : 'text-zinc-300 hover:text-white hover:bg-[#272522]'
            }`}
          >
            <Mail className="w-3.5 h-3.5 text-[#d97757]" />
            <span>Hộp thư</span>
          </button>

          <button
            title="Tài liệu quy trình"
            onClick={() => onChangeView?.('raw-documents')}
            className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left transition-colors font-medium cursor-pointer ${
              activeView === 'raw-documents'
                ? 'bg-[#272522] text-[#d97757] font-semibold'
                : 'text-zinc-300 hover:text-white hover:bg-[#272522]'
            }`}
          >
            <BookOpen className={`w-3.5 h-3.5 ${activeView === 'raw-documents' ? 'text-[#d97757]' : 'text-zinc-400'}`} />
            <span>Tài liệu quy trình</span>
          </button>
        </div>

        <div className="mt-2 flex flex-col text-xs flex-1 min-h-0">
          <div className="flex items-center justify-between px-2.5 mb-1 text-[11px] font-semibold tracking-wider text-zinc-500 shrink-0">
            <span>DỰ ÁN</span>
            <button onClick={onCreateProject} title="Tạo dự án" className="rounded p-0.5 text-zinc-400 hover:bg-[#2c2a26] hover:text-zinc-100 cursor-pointer">
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto pr-1 space-y-0.5 custom-scrollbar min-h-0">
            {projects.map((project) => {
              const isProjectExpanded = expandedProjects.has(project.id);
              const isActive = activeProjectId === project.id;
              const chats = recentChats.filter(
                (chat) => chat.projectId === project.id || (!chat.projectId && project.isDefault)
              );
              return (
                <div key={project.id}>
                  <div
                    className={`group flex items-center rounded-lg pr-1 transition-colors ${
                      isActive ? 'bg-[#2a2824] text-white font-medium border border-zinc-700/40 shadow-xs' : 'hover:bg-[#24221f] text-zinc-300'
                    }`}
                  >
                    <button
                      onClick={() =>
                        setExpandedProjects((current) => {
                          const next = new Set(current);
                          if (isProjectExpanded) next.delete(project.id);
                          else next.add(project.id);
                          return next;
                        })
                      }
                      aria-label={`${isProjectExpanded ? 'Collapse' : 'Expand'} ${project.name}`}
                      className="p-1.5 text-zinc-500 hover:text-zinc-200 cursor-pointer shrink-0"
                    >
                      {isProjectExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    </button>
                    <button
                      onClick={() => {
                        onSelectProject(project.id);
                        setExpandedProjects((current) => {
                          const next = new Set(current);
                          if (isProjectExpanded) next.delete(project.id);
                          else next.add(project.id);
                          return next;
                        });
                      }}
                      className="flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left text-xs font-medium cursor-pointer"
                    >
                      <Folder className="h-3.5 w-3.5 shrink-0" style={{ color: project.color || '#d97757' }} fill="currentColor" />
                      <span className="truncate">{project.icon && project.icon !== '📁' ? `${project.icon} ` : ''}{project.name}</span>
                    </button>
                    {!project.isDefault && onDeleteProject && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeleteProject(project);
                        }}
                        className="rounded p-1 text-zinc-500 opacity-0 hover:bg-red-950/40 hover:text-red-300 focus:opacity-100 group-hover:opacity-100 shrink-0 cursor-pointer"
                        title={`Xóa ${project.name}`}
                        aria-label={`Xóa ${project.name}`}
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                  {isProjectExpanded && (
                    <div className="ml-5 border-l border-zinc-700/50 pl-1.5 mt-0.5 space-y-0.5 max-h-52 overflow-y-auto overflow-x-hidden custom-scrollbar pr-0.5">
                      {chats.map((chat) => (
                        <div key={chat.id} className="group flex items-center gap-1 min-w-0">
                          <button
                            data-testid="project-chat"
                            data-chat-id={chat.id}
                            onMouseEnter={() => onPrefetchChat?.(chat)}
                            onFocus={() => onPrefetchChat?.(chat)}
                            onClick={() => {
                              onChangeView?.('chat');
                              onSelectRecent(chat);
                            }}
                            className={`flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors cursor-pointer ${
                              activeChatId === chat.id
                                ? 'bg-[#2f2d29] text-white font-medium'
                                : 'text-[#949089] hover:bg-[#24221f] hover:text-zinc-200'
                            }`}
                            title={chat.title}
                          >
                            <ChatLifecycleIndicator
                              chat={chat}
                              isActive={activeChatId === chat.id}
                              legacyGenerating={isGenerating && activeChatId === chat.id}
                            />
                            <span className="min-w-0 flex-1 truncate">{chat.title}</span>
                          </button>
                          <button
                            onClick={() => onDeleteChat(chat)}
                            disabled={
                              chat.generationStatus === 'generating'
                              || (!chat.generationStatus && isGenerating && activeChatId === chat.id)
                            }
                            className="rounded p-1 text-zinc-500 opacity-0 hover:bg-red-950/40 hover:text-red-300 focus:opacity-100 group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-30 shrink-0"
                            title={`Xóa ${chat.title}`}
                            aria-label={`Xóa ${chat.title}`}
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        </div>
                      ))}
                      {chats.length === 0 && (
                        <p className="px-2 py-1 text-[11px] text-zinc-500 italic">Chưa có cuộc trò chuyện nào</p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="pt-2 border-t border-[#2b2926] mt-2 space-y-2">
        <div className="flex items-center justify-between p-1.5 rounded-lg hover:bg-[#272522] transition-colors cursor-pointer group">
          <div className="flex items-center gap-2.5">
            <div className="w-6 h-6 rounded-full bg-[#35332f] text-zinc-200 text-[11px] font-semibold flex items-center justify-center border border-zinc-700/40">
              s
            </div>
            <span className="text-xs font-medium text-zinc-300 group-hover:text-white">
              steven · <span className="text-zinc-500">Pro</span>
            </span>
          </div>

        </div>
      </div>
    </aside>
  );
};
