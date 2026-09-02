/** Formats a millisecond span as Vietnamese seconds, e.g. 3842 -> "3,8". */
export function formatSecondsVi(milliseconds: number): string {
  return (milliseconds / 1000).toFixed(1).replace('.', ',');
}

/** Formats a millisecond span as short seconds, e.g. 3842 -> "3,8s" or 2000 -> "2s". */
export function formatShortSecondsVi(milliseconds: number): string {
  const sec = milliseconds / 1000;
  if (Number.isInteger(sec)) {
    return `${sec}s`;
  }
  return `${sec.toFixed(1).replace('.', ',')}s`;
}

/** Milliseconds between two ISO timestamps, or null when either is missing/invalid. */
export function spanMilliseconds(startedAt?: string, completedAt?: string): number | null {
  if (!startedAt || !completedAt) return null;
  const span = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(span) || span < 0) return null;
  return span;
}
