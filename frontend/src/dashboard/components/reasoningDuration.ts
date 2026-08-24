/** Formats a millisecond span as Vietnamese seconds, e.g. 3842 -> "3,8". */
export function formatSecondsVi(milliseconds: number): string {
  return (milliseconds / 1000).toFixed(1).replace('.', ',');
}

/** Milliseconds between two ISO timestamps, or null when either is missing/invalid. */
export function spanMilliseconds(startedAt?: string, completedAt?: string): number | null {
  if (!startedAt || !completedAt) return null;
  const span = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(span) || span < 0) return null;
  return span;
}
