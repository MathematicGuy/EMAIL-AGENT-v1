import { useEffect, useRef, useState } from 'react';
import type { AgentResult, DemoScenario } from '../types';

export type JobRunState = 'idle' | 'running' | 'done';

/**
 * Simulates the async job lifecycle described in module-2 §8 (progress /
 * heartbeat while a job runs) purely on the client, driven by the selected
 * mock AgentResult. There is no live backend call here — this is the
 * standalone UI demo required by module-2 §20 ("UI demo cho upload/select
 * fixture, operation, progress, finding/evidence và quality report").
 *
 * The caller is expected to remount this hook's owning component (e.g. via
 * `key={scenario.id}`) when the selected fixture changes, so run state
 * always starts fresh for a newly selected scenario without any
 * render-time ref bookkeeping.
 */
export function useDocumentJobDemo(scenario: DemoScenario | null) {
  const [state, setState] = useState<JobRunState>('idle');
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<AgentResult | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearTimer = () => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  };

  const reset = () => {
    clearTimer();
    setState('idle');
    setProgress(0);
    setResult(null);
  };

  const run = () => {
    if (!scenario) return;
    clearTimer();
    setState('running');
    setProgress(0);
    setResult(null);

    intervalRef.current = setInterval(() => {
      setProgress((prev) => {
        const next = Math.min(prev + 20, 100);
        if (next >= 100) {
          clearTimer();
          setState('done');
          setResult(scenario.result);
        }
        return next;
      });
    }, 150);
  };

  // Clear the interval on unmount only (no setState here — safe in an effect).
  useEffect(() => clearTimer, []);

  return { state, progress, result, run, reset };
}
