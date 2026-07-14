import type { SearchSource } from '../../api';

export type SourceDraft = {
  name: string;
  url: string;
  monitorMode: SearchSource['monitor_mode'];
  intervalSeconds: string;
  jitterPercent: string;
  stopAfterVintedSessionUses: string;
  windowStart: string;
  windowEnd: string;
  sessionDurationMinutes: string;
  filterTerms: string;
};

export function buildSourceDrafts(sources: SearchSource[]): Record<number, SourceDraft> {
  return Object.fromEntries(sources.map((source) => [source.id, buildSourceDraft(source)]));
}

export function buildSourceDraft(source: SearchSource): SourceDraft {
  const config = source.scheduler_config ?? {};
  const [windowStart, windowEnd] = splitWindow(config.allowed_windows?.[0]);
  return {
    name: source.name,
    url: source.url,
    monitorMode: source.monitor_mode ?? 'manual',
    intervalSeconds: String(config.interval_seconds ?? 300),
    jitterPercent: String(config.jitter_percent ?? 20),
    stopAfterVintedSessionUses: config.stop_after_vinted_session_uses ? String(config.stop_after_vinted_session_uses) : '',
    windowStart,
    windowEnd,
    sessionDurationMinutes: String(source.duration_minutes ?? 60),
    filterTerms: filterTermsToInput(source.filter_definition?.blacklist_terms ?? [])
  };
}

export function parseFilterTerms(value: string): string[] {
  return normalizeFilterTerms(value.split(/[\n,]+/));
}

export function filterTermsToInput(terms: string[]): string {
  return normalizeFilterTerms(terms).join('\n');
}

export function sourceDraftHasChanges(source: SearchSource, draft: SourceDraft): boolean {
  return draftFingerprint(draft) !== draftFingerprint(buildSourceDraft(source));
}

export function filterTermLabelFromDraft(draft: SourceDraft): string {
  return filterTermLabel(parseFilterTerms(draft.filterTerms));
}

export function filterTermLabelFromSource(source: SearchSource): string {
  return filterTermLabel(source.filter_definition?.blacklist_terms ?? []);
}

function draftFingerprint(draft: SourceDraft): string {
  const payload: Record<string, string | string[]> = {
    name: draft.name.trim(),
    url: draft.url.trim(),
    mode: draft.monitorMode,
    filters: parseFilterTerms(draft.filterTerms)
  };
  if (draft.monitorMode !== 'manual') {
    payload.intervalSeconds = draft.intervalSeconds.trim();
    payload.jitterPercent = draft.jitterPercent.trim();
    payload.stopAfterVintedSessionUses = draft.stopAfterVintedSessionUses.trim();
  }
  if (draft.monitorMode === 'duration') {
    payload.sessionDurationMinutes = draft.sessionDurationMinutes.trim();
  }
  if (draft.monitorMode === 'window') {
    payload.windowStart = draft.windowStart.trim();
    payload.windowEnd = draft.windowEnd.trim();
  }
  return JSON.stringify(payload);
}

function normalizeFilterTerms(terms: string[]): string[] {
  return listUnique(terms.map((term) => String(term).trim()).filter(Boolean));
}

function listUnique(values: string[]): string[] {
  return [...new Set(values)];
}

function filterTermLabel(terms: string[]): string {
  const normalized = normalizeFilterTerms(terms);
  if (normalized.length === 0) {
    return 'sin filtros';
  }
  if (normalized.length <= 3) {
    return normalized.join(', ');
  }
  return `${normalized.slice(0, 3).join(', ')} +${normalized.length - 3}`;
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
