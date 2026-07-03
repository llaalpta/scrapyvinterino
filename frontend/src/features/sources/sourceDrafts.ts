import type { SearchSource } from '../../api';

export type SourceDraft = {
  monitorMode: SearchSource['monitor_mode'];
  intervalSeconds: string;
  jitterPercent: string;
  windowStart: string;
  windowEnd: string;
  sessionDurationMinutes: string;
};

export function buildSourceDrafts(sources: SearchSource[]): Record<number, SourceDraft> {
  return Object.fromEntries(sources.map((source) => [source.id, buildSourceDraft(source)]));
}

export function buildSourceDraft(source: SearchSource): SourceDraft {
  const config = source.scheduler_config ?? {};
  const [windowStart, windowEnd] = splitWindow(config.allowed_windows?.[0]);
  return {
    monitorMode: source.monitor_mode ?? 'manual',
    intervalSeconds: String(config.interval_seconds ?? 300),
    jitterPercent: String(config.jitter_percent ?? 20),
    windowStart,
    windowEnd,
    sessionDurationMinutes: String(source.duration_minutes ?? 60)
  };
}

function splitWindow(value: string | undefined): [string, string] {
  if (!value) {
    return ['', ''];
  }
  const [start, end] = value.split('-');
  if (!start || !end) {
    return ['', ''];
  }
  return [start, end];
}
