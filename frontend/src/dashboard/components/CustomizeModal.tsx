import React from 'react';
import { X, Palette, Check } from 'lucide-react';
import type { ThemeOption } from '../types';

interface CustomizeModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedThemeId: string;
  onSelectTheme: (themeId: string) => void;
}

const THEMES: ThemeOption[] = [
  { id: 'warm-charcoal', name: 'Warm Charcoal', bgHex: '#1b1a17', cardHex: '#282724', accentHex: '#d97757' },
  { id: 'midnight-obsidian', name: 'Midnight Obsidian', bgHex: '#0f0f11', cardHex: '#1a1a1e', accentHex: '#6366f1' },
  { id: 'deep-emerald', name: 'Deep Emerald', bgHex: '#0d1512', cardHex: '#16221d', accentHex: '#10b981' },
  { id: 'classic-dark', name: 'Classic Dark', bgHex: '#121212', cardHex: '#1e1e1e', accentHex: '#e11d48' },
];

export const CustomizeModal: React.FC<CustomizeModalProps> = ({
  isOpen,
  onClose,
  selectedThemeId,
  onSelectTheme,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-md flex items-center justify-center p-4">
      <div className="bg-[#242320] border border-[#383632] rounded-2xl w-full max-w-md p-5 shadow-2xl space-y-4 text-zinc-100 animate-in fade-in zoom-in-95 duration-150">
        <div className="flex items-center justify-between border-b border-[#35332f] pb-3">
          <div className="flex items-center gap-2">
            <Palette className="w-4 h-4 text-[#d97757]" />
            <h3 className="font-semibold text-base text-zinc-100">Customize Theme & Styles</h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-zinc-400 hover:text-zinc-100 hover:bg-[#32302c] rounded-md transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Appearance Themes</label>
          <div className="grid grid-cols-2 gap-3 pt-1">
            {THEMES.map((theme) => {
              const isSelected = selectedThemeId === theme.id;
              return (
                <div
                  key={theme.id}
                  onClick={() => onSelectTheme(theme.id)}
                  className={`p-3 rounded-xl border cursor-pointer transition-all flex flex-col justify-between h-24 ${
                    isSelected ? 'border-[#d97757] ring-1 ring-[#d97757]' : 'border-zinc-800 hover:border-zinc-700'
                  }`}
                  style={{ backgroundColor: theme.cardHex }}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-zinc-200">{theme.name}</span>
                    {isSelected && <Check className="w-3.5 h-3.5 text-[#d97757]" />}
                  </div>

                  <div className="flex items-center gap-1.5 mt-2">
                    <div className="w-4 h-4 rounded-full border border-white/10" style={{ backgroundColor: theme.bgHex }} />
                    <div className="w-4 h-4 rounded-full border border-white/10" style={{ backgroundColor: theme.cardHex }} />
                    <div className="w-4 h-4 rounded-full" style={{ backgroundColor: theme.accentHex }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="pt-2">
          <button
            onClick={onClose}
            className="w-full py-2 bg-[#d97757] hover:bg-[#c26748] text-white font-medium text-xs rounded-xl transition-colors cursor-pointer"
          >
            Save Preferences
          </button>
        </div>
      </div>
    </div>
  );
};
