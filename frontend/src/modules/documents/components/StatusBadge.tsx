import React from 'react';
import type { AgentResultStatus } from '../types';

const STYLES: Record<AgentResultStatus, string> = {
  SUCCEEDED: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  PARTIAL: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  NEEDS_INPUT: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
  RETRYABLE_FAILURE: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
  PERMANENT_FAILURE: 'bg-rose-500/15 text-rose-400 border-rose-500/30',
  CANCELLED: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
};

export const StatusBadge: React.FC<{ status: AgentResultStatus }> = ({ status }) => (
  <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${STYLES[status]}`}>
    {status}
  </span>
);
