import React, { useCallback, useEffect, useMemo, useState } from 'react';
import type { SidebarState, ModelOption, RecentChat, ActiveDashboardView, ChatMessage, ReasoningMode } from './types';
import type { Project } from './types/projectTypes';
import { AVAILABLE_MODELS } from './data/mockData';
import { useStreamingChat } from './hooks/useStreamingChat';
import { Taskbar } from './components/Taskbar';
import { Header } from './components/Header';
import { HeroSection } from './components/HeroSection';
import { ChatStreamView } from './components/ChatStreamView';

import { MailInboxView } from './components/MailInboxView';
import { ArtifactsView } from './components/ArtifactsView';
import { RawDocumentsView } from './components/RawDocumentsView';
import { ModelSelectorModal } from './components/ModelSelectorModal';
import { ExecutionTraceDrawer } from './components/ExecutionTraceDrawer';
import { VoiceModal } from './components/VoiceModal';
import { useProjects } from './hooks/useProjects';
import { NewProjectModal } from './components/NewProjectModal';
import { ProjectDocumentPanel } from '../modules/project-documents/ProjectDocumentPanel';
import { areProjectDocumentsEnabled } from '../modules/project-documents/api';

interface DashboardProps {
  onNavigateHome?: () => void;
}

export const Dashboard: React.FC<DashboardProps> = ({ onNavigateHome }) => {

  const [activeView, setActiveView] = useState<ActiveDashboardView>(() => {
    const viewParam = new URLSearchParams(window.location.search).get('view');
    if (viewParam === 'mail') return 'mail';
    if (viewParam === 'artifacts') return 'artifacts';
    if (viewParam === 'raw-documents' || viewParam === 'procedures') return 'raw-documents';
    return 'chat';
  });
  const [sidebarState, setSidebarState] = useState<SidebarState>('expanded');
  const [selectedModel, setSelectedModel] = useState<ModelOption>(
    () => AVAILABLE_MODELS.find((model) => model.id === 'mimo-v2.5-pro') ?? AVAILABLE_MODELS[0],
  );
  const [reasoningMode, setReasoningMode] = useState<ReasoningMode>('fast');
  const [selectedTraceMessage, setSelectedTraceMessage] = useState<ChatMessage | null>(null);
  const [modelAnchor, setModelAnchor] = useState<Pick<
    DOMRect,
    'left' | 'right' | 'top' | 'bottom'
  > | null>(null);

  const [isModelModalOpen, setIsModelModalOpen] = useState(false);
  const [isVoiceModalOpen, setIsVoiceModalOpen] = useState(false);
  const [isNewProjectModalOpen, setIsNewProjectModalOpen] = useState(false);
  const [isProjectDocumentsOpen, setIsProjectDocumentsOpen] = useState(false);
  const [projectDocumentsEnabled, setProjectDocumentsEnabled] = useState(false);
  const [backgroundCompletion, setBackgroundCompletion] = useState<string | null>(null);
  const {
    projects,
    activeProjectId,
    setActiveProjectId,
    createProject,
    deleteProject,
    ensureDefaultProject,
  } = useProjects();
  const projectIds = useMemo(() => projects.map((project) => project.id), [projects]);
  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId),
    [projects, activeProjectId]
  );

  useEffect(() => {
    const handleOpenProjectDocs = () => {
      setIsProjectDocumentsOpen(true);
    };
    window.addEventListener('open-project-documents', handleOpenProjectDocs);
    return () => window.removeEventListener('open-project-documents', handleOpenProjectDocs);
  }, []);

  useEffect(() => {
    let dismissTimer: number | undefined;
    const handleBackgroundCompletion = (event: Event) => {
      const detail = (event as CustomEvent<{ title?: string }>).detail;
      setBackgroundCompletion(`${detail?.title || 'A chat'} finished generating.`);
      if (dismissTimer !== undefined) window.clearTimeout(dismissTimer);
      dismissTimer = window.setTimeout(() => setBackgroundCompletion(null), 5_000);
    };
    window.addEventListener('chat-background-completed', handleBackgroundCompletion);
    return () => {
      window.removeEventListener('chat-background-completed', handleBackgroundCompletion);
      if (dismissTimer !== undefined) window.clearTimeout(dismissTimer);
    };
  }, []);

  useEffect(() => {
    let active = true;
    const refreshDocumentHealth = () => {
      void areProjectDocumentsEnabled().then((enabled) => {
        if (active) setProjectDocumentsEnabled(enabled);
      });
    };
    refreshDocumentHealth();
    const timer = window.setInterval(refreshDocumentHealth, 10_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const handleSelectProject = (projectId: string) => {
    setActiveProjectId(projectId);
    resetChat();
    setActiveView('chat');
  };

  useEffect(() => {
    const handleNavigate = () => {
      setActiveView('artifacts');
    };
    window.addEventListener('navigate-to-artifacts', handleNavigate);
    return () => window.removeEventListener('navigate-to-artifacts', handleNavigate);
  }, []);

  const {
    messages,
    isGenerating,
    inputText,
    setInputText,
    selectedAttachments,
    attachmentError,
    selectAttachments,
    removeAttachment,
    sendMessage,
    stopGeneration,
    resetChat,
    deleteChat,
    loadExistingChat,
    loadFullEvidence,
    prefetchChat,
    apiStatus,
    recentChats,
    isHistoryLoading,
    isTranscriptLoading,
    activeConversationId,
    workflows,
    approveWorkflowPlan,
    reviseWorkflowPlan,
    retryWorkflowStep,
    retryTurn,
  } = useStreamingChat(selectedModel.id, activeProjectId, projectIds, reasoningMode);

  const [cachedTraceMessage, setCachedTraceMessage] = useState<ChatMessage | null>(null);
  const activeTraceMessage = selectedTraceMessage
    ? (messages.find((m) => m.id === selectedTraceMessage.id) ?? selectedTraceMessage)
    : null;

  if (activeTraceMessage && activeTraceMessage !== cachedTraceMessage) {
    setCachedTraceMessage(activeTraceMessage);
  }

  const displayedTraceMessage = activeTraceMessage ?? cachedTraceMessage;

  const handleToggleExecutionTrace = useCallback((msg: ChatMessage) => {
    setSelectedTraceMessage((prev) => (prev?.id === msg.id ? null : msg));
  }, []);

  const handleToggleSidebar = () => {
    setSidebarState((prev) => (prev === 'collapsed' ? 'expanded' : 'collapsed'));
  };

  const openModelSelector = (anchor: DOMRect) => {
    setModelAnchor({
      left: anchor.left,
      right: anchor.right,
      top: anchor.top,
      bottom: anchor.bottom,
    });
    setIsModelModalOpen(true);
  };

  const handleNewChat = async () => {
    if (!activeProjectId) await ensureDefaultProject();
    resetChat();
    setActiveView('chat');
  };

  const handleSendMessage = async (text?: string) => {
    const project = activeProject ?? await ensureDefaultProject();
    await sendMessage(text, project.id);
  };

  const handleSelectRecent = (chat: RecentChat) => {
    if (chat.projectId) setActiveProjectId(chat.projectId);
    void loadExistingChat(chat.id, chat.projectId);
    setActiveView('chat');
  };

  const handleDeleteChat = (chat: RecentChat) => {
    if (!window.confirm(`Delete “${chat.title}”? This cannot be undone.`)) return;
    void deleteChat(chat.id).catch(() => {
      window.alert('Could not delete this chat. Please try again.');
    });
  };

  const handleDeleteProject = (project: Project) => {
    if (project.isDefault) return;
    if (!window.confirm(`Delete project “${project.name}”? All associated chats and documents will be removed.`)) return;
    void deleteProject(project.id).catch(() => {
      window.alert('Could not delete this project. Please try again.');
    });
  };

  return (
    <div className="h-screen w-screen flex bg-[#1b1a17] text-[#f3f2ef] overflow-hidden">
      <Taskbar
        sidebarState={sidebarState}
        onToggleSidebar={handleToggleSidebar}
        onNewChat={handleNewChat}
        onNewChatInProject={handleSelectProject}
        onCreateProject={() => setIsNewProjectModalOpen(true)}
        projects={projects}
        activeProjectId={activeProjectId}
        onSelectProject={handleSelectProject}
        onDeleteProject={handleDeleteProject}
        onSelectRecent={handleSelectRecent}
        onPrefetchChat={(chat) => { void prefetchChat(chat.id); }}
        onDeleteChat={handleDeleteChat}
        recentChats={recentChats}
        isHistoryLoading={isHistoryLoading}
        activeChatId={activeConversationId ?? undefined}
        onNavigateHome={onNavigateHome}
        activeView={activeView}
        onChangeView={setActiveView}
        initialExpandedProjectIds={projects.map((p) => p.id)}
        isGenerating={isGenerating}
      />

      <main className="flex-1 flex flex-col h-screen overflow-hidden relative">
        <Header
          apiStatus={apiStatus}
          projects={projects}
          activeProject={activeProject}
          onSelectProject={handleSelectProject}
          showProjectDocuments={projectDocumentsEnabled && activeView === 'chat'}
          onOpenProjectDocuments={() => setIsProjectDocumentsOpen(true)}
        />

        {activeView === 'mail' && (
          <MailInboxView />
        )}

        {activeView === 'artifacts' && (
          <ArtifactsView />
        )}

        {activeView === 'raw-documents' && (
          <RawDocumentsView />
        )}

        <div className={`flex-1 flex min-h-0 relative overflow-hidden ${activeView === 'chat' ? '' : 'hidden'}`}>
          <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
          {isTranscriptLoading || messages.length > 0 ? (
            <ChatStreamView
              messages={messages}
              isTranscriptLoading={isTranscriptLoading}
              inputText={inputText}
              onChangeText={setInputText}
              onSend={handleSendMessage}
              isGenerating={isGenerating}
              onStopGeneration={stopGeneration}
              selectedModel={selectedModel}
              onOpenModelModal={openModelSelector}
              onOpenVoiceModal={() => setIsVoiceModalOpen(true)}
              attachments={selectedAttachments}
              attachmentError={attachmentError}
              onSelectFiles={projectDocumentsEnabled ? selectAttachments : undefined}
              onRemoveAttachment={removeAttachment}
              workflows={workflows}
              onApproveWorkflowPlan={approveWorkflowPlan}
              onReviseWorkflowPlan={reviseWorkflowPlan}
              onRetryWorkflowStep={(taskId, stepId) =>
                void retryWorkflowStep(taskId, stepId)
              }
              onRetryTurn={retryTurn}
              onLoadFullEvidence={loadFullEvidence}
              activeProject={activeProject}
              projects={projects}
              onSelectProject={handleSelectProject}
              onOpenMailInbox={() => setActiveView('mail')}
              selectedTraceMessageId={selectedTraceMessage?.id}
              onOpenExecutionTrace={handleToggleExecutionTrace}
            />
          ) : (
            <HeroSection
              inputText={inputText}
              onChangeText={setInputText}
              onSend={handleSendMessage}
              isGenerating={isGenerating}
              selectedModel={selectedModel}
              onOpenModelModal={openModelSelector}
              onOpenVoiceModal={() => setIsVoiceModalOpen(true)}
              attachments={selectedAttachments}
              attachmentError={attachmentError}
              onSelectFiles={projectDocumentsEnabled ? selectAttachments : undefined}
              onRemoveAttachment={removeAttachment}
              activeProject={activeProject}
              projects={projects}
              onSelectProject={handleSelectProject}
            />
          )}
          </div>

          {/* Smooth Slide-in / Slide-out Execution Trace Drawer */}
          <div
            className={`min-h-0 flex flex-col transition-all duration-300 ease-in-out overflow-hidden z-20 shrink-0 border-[#413b34] bg-[#201e1b] ${
              selectedTraceMessage
                ? 'w-[360px] sm:w-[420px] max-w-[90vw] border-l opacity-100'
                : 'w-0 border-l-0 opacity-0 pointer-events-none'
            }`}
          >
            {displayedTraceMessage && (
              <div className="w-[360px] sm:w-[420px] max-w-[90vw] h-full flex flex-col min-h-0 overflow-hidden">
                <ExecutionTraceDrawer
                  trace={displayedTraceMessage.executionTrace}
                  activities={displayedTraceMessage.activities}
                  generationStatus={displayedTraceMessage.generationStatus}
                  onClose={() => setSelectedTraceMessage(null)}
                />
              </div>
            )}
          </div>
        </div>
      </main>

      <ModelSelectorModal
        isOpen={isModelModalOpen}
        onClose={() => setIsModelModalOpen(false)}
        selectedModel={selectedModel}
        onSelectModel={setSelectedModel}
        reasoningMode={reasoningMode}
        onSelectReasoningMode={setReasoningMode}
        anchor={modelAnchor}
      />

      <VoiceModal
        isOpen={isVoiceModalOpen}
        onClose={() => setIsVoiceModalOpen(false)}
        onSendTranscript={handleSendMessage}
      />

      <NewProjectModal
        isOpen={isNewProjectModalOpen}
        onClose={() => setIsNewProjectModalOpen(false)}
        onCreate={(name) => {
          createProject({ name });
          setIsNewProjectModalOpen(false);
          resetChat();
        }}
      />

      {projectDocumentsEnabled && (
        <ProjectDocumentPanel
          projectId={activeProjectId}
          projectName={activeProject?.name}
          isOpen={isProjectDocumentsOpen}
          onClose={() => setIsProjectDocumentsOpen(false)}
          hideTrigger
        />
      )}

      {backgroundCompletion && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-5 right-5 z-50 max-w-sm rounded-xl border border-[#4a4842] bg-[#272622] px-4 py-3 text-sm text-[#f3f2ef] shadow-xl"
        >
          {backgroundCompletion}
        </div>
      )}
    </div>
  );
};

export default Dashboard;
