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
  const greetings = ["Evening, steven", "Good afternoon, Dam Manh", "Let's noodle"];

  const toggleGreeting = () => {
    setGreetingIndex((prev) => (prev + 1) % greetings.length);
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 -mt-6 select-text overflow-y-auto custom-scrollbar py-8">
      {/* Title & F-Cowork Icon Row matching image.png */}
      <div className="flex items-center gap-3.5 mb-8 cursor-pointer group select-none" onClick={toggleGreeting} title="Click to toggle greeting">
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
      />

      {/* Ideas for you section matching image.png */}
      <div className="w-full max-w-3xl md:max-w-4xl mt-8 px-4 space-y-3">
        <div className="text-xs font-medium text-zinc-500 select-none">Ideas for you</div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
          {IDEAS_FOR_YOU.map((idea) => (
            <button
              key={idea.id}
              // In chat mode the artifact ideas only prefill the composer, so
              // Enter/Send stays the single send path. Card three and cowork
              // mode keep their original send behaviour.
              onClick={() =>
                idea.id === 'idea-3'
                  ? onSend(idea.prompt)
                  : onChangeText(idea.prompt)
              }
              className="w-full flex items-center gap-3.5 p-3 rounded-xl bg-[#23221f] hover:bg-[#2c2a26] border border-[#33312e] text-left transition-all cursor-pointer group"
            >
              <div className="w-8 h-8 rounded-lg bg-[#2b2926] border border-zinc-700/40 flex items-center justify-center text-sm shrink-0 group-hover:border-zinc-500 transition-colors">
                {idea.icon}
              </div>
              <span className="text-xs font-medium text-zinc-200 group-hover:text-white transition-colors leading-snug">
                {idea.title}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
