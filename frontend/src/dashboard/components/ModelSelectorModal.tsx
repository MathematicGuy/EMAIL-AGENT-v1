import React from 'react';
import { Check, Info } from 'lucide-react';
import type { ModelOption } from '../types';
import { AVAILABLE_MODELS } from '../data/mockData';

interface ModelSelectorModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedModel: ModelOption;
  onSelectModel: (model: ModelOption) => void;
  anchor: Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom'> | null;
}

export const ModelSelectorModal: React.FC<ModelSelectorModalProps> = ({
  isOpen,
  onClose,
  selectedModel,
  onSelectModel,
  anchor,
}) => {
  if (!isOpen || !anchor) return null;

  const panelWidth = 360;
  const viewportPadding = 8;
  const left = Math.min(
    Math.max(viewportPadding, anchor.right - panelWidth),
    window.innerWidth - panelWidth - viewportPadding
  );
  const bottom = window.innerHeight - anchor.top + 6;

  return (
    <div
      className="fixed inset-0 z-50 bg-transparent"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-selector-title"
        style={{ left, bottom, width: panelWidth }}
        className="absolute max-w-[calc(100vw-16px)] overflow-hidden rounded-lg border border-[#383838] bg-[#282828] text-zinc-200 shadow-2xl animate-in fade-in zoom-in-95 duration-100"
      >
        <div className="flex h-8 items-center border-b border-[#393939] bg-[#262626] px-2.5">
          <h3
            id="model-selector-title"
            className="text-[13px] font-medium text-zinc-500"
          >
            Mô hình
          </h3>
        </div>

        <div className="max-h-[72vh] overflow-y-auto py-0.5">
          {AVAILABLE_MODELS.map((model) => {
            const isSelected = selectedModel.id === model.id;
            const speedLabel = model.id.startsWith('gemini')
              ? 'Nhanh'
              : 'Suy luận';

            return (
              <button
                key={model.id}
                type="button"
                title={model.description}
                aria-pressed={isSelected}
                onClick={() => {
                  onSelectModel(model);
                  onClose();
                }}
                className={`flex h-8 w-full items-center gap-2 px-2.5 text-left text-[13px] transition-colors ${
                  isSelected
                    ? 'bg-[#3a3a3a] text-zinc-100'
                    : 'text-zinc-300 hover:bg-[#333333] hover:text-zinc-100'
                }`}
              >
                <span className="min-w-0 flex-1 truncate">{model.name}</span>
                <span className="flex shrink-0 items-center gap-1 rounded-full bg-[#333333] px-2 py-0.5 text-[12px] text-zinc-500">
                  {speedLabel}
                  <Info className="h-3 w-3" aria-hidden="true" />
                </span>
                {isSelected && (
                  <Check
                    className="h-3.5 w-3.5 shrink-0 text-zinc-400"
                    aria-hidden="true"
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
};
