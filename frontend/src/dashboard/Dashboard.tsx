import React, { useEffect, useMemo, useState } from 'react';
import type { SidebarState, ModelOption, RecentChat, ActiveDashboardView } from './types';
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
  const [selectedModel, setSelectedModel] = useState<ModelOption>(AVAILABLE_MODELS[0]);
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
  const { projects, activeProjectId, setActiveProjectId, createProject, ensureDefaultProject } = useProjects();
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
  } = useStreamingChat(selectedModel.id, activeProjectId, projectIds);

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
        onSelectRecent={handleSelectRecent}
        onPrefetchChat={(chat) => { void prefetchChat(chat.id); }}
        onDeleteChat={handleDeleteChat}
        recentChats={recentChats}
        isHistoryLoading={isHistoryLoading}
        activeChatId={activeConversationId ?? undefined}
        onNavigateHome={onNavigateHome}
        activeView={activeView}
        onChangeView={setActiveView}
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

        <div className={`flex-1 flex flex-col min-h-0 ${activeView === 'chat' ? '' : 'hidden'}`}>
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
      </main>

      <ModelSelectorModal
        isOpen={isModelModalOpen}
        onClose={() => setIsModelModalOpen(false)}
        selectedModel={selectedModel}
        onSelectModel={setSelectedModel}
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
