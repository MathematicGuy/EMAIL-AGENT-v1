import React, { useState } from 'react';
import {
  Loader2,
  Wifi,
  WifiOff,
  Folder,
  ChevronDown,
  Check,
  FileText,
} from 'lucide-react';
import type { Project } from '../types/projectTypes';
import { documentText } from '../../modules/project-documents/i18n';

interface HeaderProps {
  apiStatus?: 'unknown' | 'online' | 'offline';
  projects?: Project[];
  activeProject?: Project;
  onSelectProject?: (projectId: string) => void;
  showProjectDocuments?: boolean;
  onOpenProjectDocuments?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  apiStatus,
  projects,
  activeProject,
  onSelectProject,
  showProjectDocuments,
  onOpenProjectDocuments,
}) => {
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);

  return (
    <header className="h-10 w-full flex items-center justify-between px-4 z-20 select-none bg-[#1b1a17] shrink-0 border-b border-[#292723]">
      {/* Left: API status indicator & Active Project Selector */}
      <div className="flex items-center gap-3">
        {apiStatus === 'online' && (
          <span className="flex items-center gap-1 text-[10px] font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded-full">
            <Wifi className="w-2.5 h-2.5" /> API Hoạt động
          </span>
        )}
        {apiStatus === 'offline' && (
          <span className="flex items-center gap-1 text-[10px] font-semibold text-rose-400 bg-rose-500/10 border border-rose-500/20 px-2 py-0.5 rounded-full">
            <WifiOff className="w-2.5 h-2.5" /> API Ngoại tuyến
          </span>
        )}
        {apiStatus === 'unknown' && (
          <span className="flex items-center gap-1 text-[10px] font-semibold text-zinc-400 bg-zinc-500/10 border border-zinc-500/20 px-2 py-0.5 rounded-full">
            <Loader2 className="w-2.5 h-2.5 animate-spin" /> Đang kết nối…
          </span>
        )}

        {/* Project Selector Badge */}
        {activeProject && (
          <div className="relative">
            <button
              onClick={() => setIsDropdownOpen(!isDropdownOpen)}
              className="flex items-center gap-1.5 px-2.5 py-1 bg-[#252320] hover:bg-[#2e2b27] border border-[#383531] rounded-lg text-xs font-medium text-zinc-200 transition-colors cursor-pointer shadow-xs"
              title="Dự án hiện tại"
            >
              <Folder
                className="w-3.5 h-3.5 shrink-0"
                style={{ color: activeProject.color || '#d97757' }}
                fill="currentColor"
              />
              <span className="max-w-[150px] truncate font-medium">
                {activeProject.name}
              </span>
              <ChevronDown className="w-3 h-3 text-zinc-400 shrink-0" />
            </button>

            {isDropdownOpen && (
              <>
                <div
                  className="fixed inset-0 z-40"
                  onClick={() => setIsDropdownOpen(false)}
                />
                <div className="absolute left-0 mt-1 w-56 bg-[#22201d] border border-[#383531] rounded-xl shadow-2xl z-50 py-1.5 text-xs select-none">
                  <div className="px-3 py-1 text-[10px] font-semibold tracking-wider text-zinc-500 uppercase">
                    Dự án hiện tại
                  </div>
                  {projects?.map((project) => (
                    <button
                      key={project.id}
                      onClick={() => {
                        onSelectProject?.(project.id);
                        setIsDropdownOpen(false);
                      }}
                      className={`w-full flex items-center justify-between px-3 py-1.5 hover:bg-[#2c2a26] text-left transition-colors cursor-pointer ${
                        activeProject.id === project.id ? 'text-white font-semibold bg-[#2a2825]' : 'text-zinc-300'
                      }`}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <Folder
                          className="w-3.5 h-3.5 shrink-0"
                          style={{ color: project.color || '#d97757' }}
                          fill="currentColor"
                        />
                        <span className="truncate">
                          {project.icon && project.icon !== '📁' ? `${project.icon} ` : ''}
                          {project.name}
                        </span>
                      </div>
                      {activeProject.id === project.id && (
                        <Check className="w-3.5 h-3.5 text-[#d97757] shrink-0" />
                      )}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-2">
        {showProjectDocuments && (
          <button
            type="button"
            title={documentText('title')}
            onClick={onOpenProjectDocuments}
            disabled={!activeProject?.id}
            className="flex items-center gap-1.5 px-2.5 py-1 bg-[#252320] hover:bg-[#2e2b27] border border-[#383531] rounded-lg text-xs font-medium text-zinc-300 hover:text-zinc-100 transition-colors cursor-pointer disabled:opacity-40 shadow-xs"
          >
            <FileText className="w-3.5 h-3.5 text-[#d97757]" />
            <span>{documentText('title')}</span>
          </button>
        )}
      </div>
    </header>
  );
};
