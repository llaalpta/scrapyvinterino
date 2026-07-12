import { useState } from 'react';
import { type Run, type RunEvent } from '../../api';

export type RunActivityController = {
  eventsByRunId: Record<number, RunEvent[]>;
  loadingRunId: number | null;
  openRunId: number | null;
  streamStatus: 'historical';
  toggleLogs: (runId: number) => Promise<void>;
};

export function useRunActivity(
  runs: Run[],
  onLoadRunEvents: (runId: number) => Promise<RunEvent[]>
): RunActivityController {
  const [openRunId, setOpenRunId] = useState<number | null>(null);
  const [eventsByRunId, setEventsByRunId] = useState<Record<number, RunEvent[]>>({});
  const [loadingRunId, setLoadingRunId] = useState<number | null>(null);
  async function toggleLogs(runId: number) {
    if (openRunId === runId) {
      setOpenRunId(null);
      return;
    }
    setOpenRunId(runId);
    if (eventsByRunId[runId]) {
      return;
    }
    setLoadingRunId(runId);
    try {
      const events = await onLoadRunEvents(runId);
      setEventsByRunId((current) => ({ ...current, [runId]: events }));
    } finally {
      setLoadingRunId(null);
    }
  }

  return { eventsByRunId, loadingRunId, openRunId, streamStatus: 'historical', toggleLogs };
}
