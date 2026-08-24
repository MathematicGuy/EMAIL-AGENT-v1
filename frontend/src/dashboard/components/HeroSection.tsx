import React, { useState } from 'react';
import { ChatInputBox } from './ChatInputBox';
import type {
  ChatComposerAttachment,
  ModelOption,
} from '../types';
import type { Project } from '../types/projectTypes';
import { IDEAS_FOR_YOU } from '../data/mockData';

interface HeroSectionProps {
  inputText: string;
  onChangeText: (text: string) => void;
  onSend: (text?: string) => void;
  isGenerating?: boolean;
  selectedModel: ModelOption;
  onOpenModelModal: (anchor: DOMRect) => void;
  onOpenVoiceModal: () => void;

  attachments?: ChatComposerAttachment[];
  attachmentError?: string | null;
  onSelectFiles?: (files: File[]) => void;
  onRemoveAttachment?: (attachmentId: string) => void;
  activeProject?: Project;
  projects?: Project[];
  onSelectProject?: (projectId: string) => void;
}

// Brand Icon component rendering official f-cowork-icon.svg
export const StarburstIcon = ({ className = "w-6 h-6" }: { className?: string }) => (
  <img
    src="/images/f-cowork-icon.svg"
    alt="F-Cowork Brand Icon"
    className={`${className} object-contain inline-block`}
  />
);

export const HeroSection: React.FC<HeroSectionProps> = ({
  inputText,
  onChangeText,
  onSend,
  isGenerating,
  selectedModel,
  onOpenModelModal,
  onOpenVoiceModal,

  attachments,
  attachmentError,
  onSelectFiles,
  onRemoveAttachment,
  activeProject,
  projects,
  onSelectProject,
}) => {
  const [greetingIndex, setGreetingIndex] = useState(0);
  const greetings = ["Chào buổi tối, steven", "Chào buổi chiều, Đàm Mạnh", "Cùng lên ý tưởng nào"];

  const toggleGreeting = () => {
    setGreetingIndex((prev) => (prev + 1) % greetings.length);
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 -mt-6 select-text overflow-y-auto custom-scrollbar py-8">
      {/* Title & F-Cowork Icon Row matching image.png */}
      <div className="flex items-center gap-3.5 mb-8 cursor-pointer group select-none" onClick={toggleGreeting} title="Nhấp để đổi lời chào">
        <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-xl bg-[#282623] border border-[#383531] p-1.5 shadow-md group-hover:scale-105 transition-transform duration-200 flex items-center justify-center">
          <StarburstIcon className="w-full h-full" />
        </div>
        <h1 className="font-display-serif text-3xl sm:text-4xl md:text-[42px] tracking-tight text-[#f3f2ef] font-normal font-serif">
          {greetings[greetingIndex]}
        </h1>
      </div>

      {/* Floating Input Box */}
      <ChatInputBox
        inputText={inputText}
        onChangeText={onChangeText}
        onSend={onSend}
        isGenerating={isGenerating}
        selectedModel={selectedModel}
        onOpenModelModal={onOpenModelModal}
        onOpenVoiceModal={onOpenVoiceModal}

        attachments={attachments}
        attachmentError={attachmentError}
        onSelectFiles={onSelectFiles}
        onRemoveAttachment={onRemoveAttachment}
        activeProject={activeProject}
        projects={projects}
        onSelectProject={onSelectProject}
        promptSuggestions={IDEAS_FOR_YOU}
      />
    </div>
  );
};
