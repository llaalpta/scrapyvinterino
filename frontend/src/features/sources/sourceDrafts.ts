import type { SearchSource } from '../../api';

export type SourceDraft = {
  intervalSeconds: string;
  jitterPercent: string;
  allowedWindows: string;
};

export function buildSourceDrafts(sources: SearchSource[]): Record<number, SourceDraft> {
  return Object.fromEntries(sources.map((source) => [source.id, buildSourceDraft(source)]));
}

export function buildSourceDraft(source: SearchSource): SourceDraft {
  const config = source.scheduler_config ?? {};
  return {
    intervalSeconds: String(config.interval_seconds ?? 300),
    jitterPercent: String(config.jitter_percent ?? 20),
    allowedWindows: (config.allowed_windows ?? []).join(', ')
  };
}
