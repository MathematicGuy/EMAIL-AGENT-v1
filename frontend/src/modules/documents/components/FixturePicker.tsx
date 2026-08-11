import React from 'react';
import { FileText } from 'lucide-react';
import type { DemoScenario, Operation } from '../types';

const OPERATION_LABEL: Record<Operation, string> = {
  parse: 'Parse',
  extract: 'Extract',
  analyze: 'Analyze',
  query: 'Query',
};

export const FixturePicker: React.FC<{
  scenarios: DemoScenario[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}> = ({ scenarios, selectedId, onSelect }) => (
  <div className="space-y-1.5">
    {scenarios.map((scenario) => {
      const isSelected = scenario.id === selectedId;
      return (
        <button
          key={scenario.id}
          onClick={() => onSelect(scenario.id)}
          className={`flex w-full items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors ${
            isSelected
              ? 'border-[#d97757]/60 bg-[#d97757]/10'
              : 'border-[#33312e] bg-[#22211e] hover:bg-[#292825]'
          }`}
        >
          <FileText className={`mt-0.5 h-4 w-4 shrink-0 ${isSelected ? 'text-[#d97757]' : 'text-[#6c6862]'}`} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-medium text-[#f3f2ef]">{scenario.fixtureName}</span>
              <span className="shrink-0 rounded-full border border-[#33312e] px-1.5 py-0.5 text-[10px] text-[#949089]">
                {OPERATION_LABEL[scenario.operation]}
              </span>
            </div>
            <p className="mt-0.5 truncate text-xs text-[#6c6862]">{scenario.description}</p>
          </div>
        </button>
      );
    })}
  </div>
);
