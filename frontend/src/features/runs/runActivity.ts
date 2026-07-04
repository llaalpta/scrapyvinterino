import { useEffect, useState } from 'react';
import { monitorEventsStreamUrl, type Run, type RunEvent } from '../../api';

export type RunActivityController = {
  eventsByRunId: Record<number, RunEvent[]>;
  loadingRunId: number | null;
  openRunId: number | null;
  streamStatus: 'connecting' | 'connected' | 'error';
  toggleLogs: (runId: number) => Promise<void>;
};

export type RunActivityOptions = {
  onRunEvent?: (event: RunEvent) => void;
  streamEnabled?: boolean;
};

export function useRunActivity(
  runs: Run[],
  onLoadRunEvents: (runId: number) => Promise<RunEvent[]>,
  options: RunActivityOptions = {}
): RunActivityController {
  const [openRunId, setOpenRunId] = useState<number | null>(null);
  const [eventsByRunId, setEventsByRunId] = useState<Record<number, RunEvent[]>>({});
  const [loadingRunId, setLoadingRunId] = useState<number | null>(null);
  const [streamStatus, setStreamStatus] = useState<'connecting' | 'connected' | 'error'>('connecting');
  const streamEnabled = options.streamEnabled ?? runs.length > 0;
  const onRunEvent = options.onRunEvent;

  useEffect(() => {
    if (!streamEnabled) {
      return undefined;
    }

    const events = new EventSource(monitorEventsStreamUrl());
    events.addEventListener('open', () => setStreamStatus('connected'));
    events.addEventListener('error', () => setStreamStatus('error'));
    events.addEventListener('monitor_event', (message) => {
      const event = parseRunEvent(message);
      if (!event?.run_id) {
        return;
      }
      setEventsByRunId((current) => mergeRunEvent(current, event));
      onRunEvent?.(event);
    });

    return () => events.close();
  }, [onRunEvent, streamEnabled]);

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

  return { eventsByRunId, loadingRunId, openRunId, streamStatus, toggleLogs };
}

function parseRunEvent(message: MessageEvent): RunEvent | null {
  try {
    return JSON.parse(message.data) as RunEvent;
  } catch {
    return null;
  }
}

function mergeRunEvent(current: Record<number, RunEvent[]>, event: RunEvent): Record<number, RunEvent[]> {
  if (!event.run_id) {
    return current;
  }
  const existing = current[event.run_id] ?? [];
  if (existing.some((entry) => entry.id === event.id)) {
    return current;
  }
  return {
    ...current,
    [event.run_id]: [...existing, event].sort((left, right) => left.id - right.id)
  };
}
