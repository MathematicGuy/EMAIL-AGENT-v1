import React from 'react';

export const ProgressBar: React.FC<{ percent: number; label?: string }> = ({ percent, label }) => (
  <div className="w-full">
    {label && <div className="mb-1 text-xs text-[#949089]">{label}</div>}
    <div className="h-2 w-full overflow-hidden rounded-full bg-[#33312e]" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
      <div
        className="h-full rounded-full bg-[#d97757] transition-all duration-150 ease-out"
        style={{ width: `${percent}%` }}
      />
    </div>
  </div>
);
