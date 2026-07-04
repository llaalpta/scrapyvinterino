import { Play, Save, Square, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, type FormEvent } from 'react';
import type { FilterRule, ProxyProfile, Run, RunEvent, SearchSource } from '../../api';
import { formatDate } from '../../utils/format';
import { RunActivityList } from '../runs/RunsView';
import { useRunActivity } from '../runs/runActivity';
import { buildSourceDraft, type SourceDraft } from './sourceDrafts';

export function SourcesView({
  filterRules,
  onCreateSource,
  onDeleteSource,
  onLoadRunEvents,
  onRefreshRuntime,
  onRunMonitor,
  onSaveSourceSchedule,
  onStartSession,
  onStopMonitor,
  proxyProfiles,
  runningSessionId,
  runs,
  savingSourceId,
  selectedFilterIdsBySource,
  selectedProxyBySource,
  sourceDrafts,
  sourceName,
  sources,
  sourceUrl,
  setSourceName,
  setSourceUrl,
  toggleSourceFilter,
  updateSourceDraft,
  updateSourceProxy
}: {
  filterRules: FilterRule[];
  onCreateSource: (event: FormEvent<HTMLFormElement>) => void;
  onDeleteSource: (source: SearchSource) => void;
  onLoadRunEvents: (runId: number) => Promise<RunEvent[]>;
  onRefreshRuntime: () => Promise<void>;
  onRunMonitor: (sourceId: number) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  onStopMonitor: (sourceId: number) => void;
  proxyProfiles: ProxyProfile[];
  runningSessionId: number | null;
  runs: Run[];
  savingSourceId: number | null;
  selectedFilterIdsBySource: Record<number, number[]>;
  selectedProxyBySource: Record<number, string>;
  sourceDrafts: Record<number, SourceDraft>;
  sourceName: string;
  sources: SearchSource[];
  sourceUrl: string;
  setSourceName: (value: string) => void;
  setSourceUrl: (value: string) => void;
  toggleSourceFilter: (sourceId: number, filterId: number) => void;
  updateSourceDraft: (sourceId: number, field: keyof SourceDraft, value: string) => void;
  updateSourceProxy: (sourceId: number, value: string) => void;
}) {
  const activeSources = useMemo(() => sources.filter((source) => source.is_active), [sources]);
  const inactiveSources = useMemo(() => sources.filter((source) => !source.is_active), [sources]);
  const activeSourceIds = useMemo(() => new Set(activeSources.map((source) => source.id)), [activeSources]);
  const activeRuns = useMemo(() => runs.filter((run) => activeSourceIds.has(run.source_id)), [activeSourceIds, runs]);
  const refreshTimerRef = useRef<number | null>(null);
  const handleRunEvent = useCallback(
    (event: RunEvent) => {
      if (!event.source_id || !activeSourceIds.has(event.source_id) || !shouldRefreshRuns(event.phase)) {
        return;
      }
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
      refreshTimerRef.current = window.setTimeout(() => {
        refreshTimerRef.current = null;
        void onRefreshRuntime();
      }, 400);
    },
    [activeSourceIds, onRefreshRuntime]
  );
  const activity = useRunActivity(activeRuns, onLoadRunEvents, {
    onRunEvent: handleRunEvent,
    streamEnabled: activeSources.length > 0
  });

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
    };
  }, []);

  function getSourceName(sourceId: number): string {
    return sources.find((source) => source.id === sourceId)?.name ?? `Monitor ${sourceId}`;
  }

  return (
    <section className="sources-panel">
      <div className="panel-heading">
        <h3>Monitores de oportunidad</h3>
        <span>{sources.length}</span>
      </div>
      <form className="source-form" onSubmit={onCreateSource}>
        <input value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="Nombre del monitor" required />
        <input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="URL de catalogo Vinted" required />
        <button type="submit">Guardar URL</button>
      </form>

      {sources.length === 0 ? <p className="empty-inline">No hay monitores configurados.</p> : null}

      {sources.length > 0 ? (
        <div className="source-sections">
          <MonitorSectionHeading label="Monitores activos" count={activeSources.length} />
          {activeSources.length === 0 ? (
            <p className="empty-inline compact">No hay monitores activos.</p>
          ) : (
            <div className="source-cards active-source-cards">
              {activeSources.map((source) => {
                const sourceRuns = activeRuns.filter((run) => run.source_id === source.id).slice(0, 3);
                return (
                  <article className="source-card active-monitor-card" key={source.id}>
                    <div className="source-card-header">
                      <div className="source-main">
                        <strong>{source.name}</strong>
                        <a href={source.url} target="_blank" rel="noreferrer">
                          {source.url}
                        </a>
                      </div>
                      <div className="source-badges">
                        <span className="status running">Activo</span>
                        <span className="status active">{modeLabel(source.monitor_mode)}</span>
                      </div>
                    </div>

                    <div className="source-config-summary">
                      {monitorSummary(source, filterRules, proxyProfiles).map((entry) => (
                        <span key={entry}>{entry}</span>
                      ))}
                    </div>

                    <div className="source-actions">
                      <button type="button" disabled={runningSessionId !== null} onClick={() => onRunMonitor(source.id)}>
                        <Play size={17} />
                        {runningSessionId === source.id ? 'Ejecutando' : 'Ejecutar ahora'}
                      </button>
                      <button type="button" disabled={savingSourceId === source.id} onClick={() => onStopMonitor(source.id)}>
                        <Square size={16} />
                        Detener monitor
                      </button>
                    </div>

                    <div className="active-monitor-activity">
                      <div className="subsection-heading">
                        <h4>Actividad reciente</h4>
                        <span>{sourceRuns.length}</span>
                      </div>
                      <RunActivityList
                        activity={activity}
                        emptyText="Sin ejecuciones recientes para este monitor."
                        getSourceName={getSourceName}
                        runs={sourceRuns}
                        variant="inline"
                      />
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          <MonitorSectionHeading label="Monitores inactivos" count={inactiveSources.length} />
          {inactiveSources.length === 0 ? (
            <p className="empty-inline compact">No hay monitores inactivos.</p>
          ) : (
            <div className="source-cards">
              {inactiveSources.map((source) => (
                <InactiveMonitorCard
                  filterRules={filterRules}
                  key={source.id}
                  onDeleteSource={onDeleteSource}
                  onSaveSourceSchedule={onSaveSourceSchedule}
                  onStartSession={onStartSession}
                  proxyProfiles={proxyProfiles}
                  runningSessionId={runningSessionId}
                  savingSourceId={savingSourceId}
                  selectedFilterIds={selectedFilterIdsBySource[source.id] ?? []}
                  selectedProxy={selectedProxyBySource[source.id] ?? ''}
                  source={source}
                  sourceDraft={sourceDrafts[source.id] ?? buildSourceDraft(source)}
                  toggleSourceFilter={toggleSourceFilter}
                  updateSourceDraft={updateSourceDraft}
                  updateSourceProxy={updateSourceProxy}
                />
              ))}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

function MonitorSectionHeading({ count, label }: { count: number; label: string }) {
  return (
    <div className="source-section-heading">
      <h4>{label}</h4>
      <span>{count}</span>
    </div>
  );
}

function InactiveMonitorCard({
  filterRules,
  onDeleteSource,
  onSaveSourceSchedule,
  onStartSession,
  proxyProfiles,
  runningSessionId,
  savingSourceId,
  selectedFilterIds,
  selectedProxy,
  source,
  sourceDraft,
  toggleSourceFilter,
  updateSourceDraft,
  updateSourceProxy
}: {
  filterRules: FilterRule[];
  onDeleteSource: (source: SearchSource) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  proxyProfiles: ProxyProfile[];
  runningSessionId: number | null;
  savingSourceId: number | null;
  selectedFilterIds: number[];
  selectedProxy: string;
  source: SearchSource;
  sourceDraft: SourceDraft;
  toggleSourceFilter: (sourceId: number, filterId: number) => void;
  updateSourceDraft: (sourceId: number, field: keyof SourceDraft, value: string) => void;
  updateSourceProxy: (sourceId: number, value: string) => void;
}) {
  const isRecurring = sourceDraft.monitorMode !== 'manual';

  return (
    <article className="source-card inactive-monitor-card">
      <div className="inactive-monitor-compact">
        <div className="source-card-header compact">
          <div className="source-main">
            <strong>{source.name}</strong>
            <a href={source.url} target="_blank" rel="noreferrer">
              {source.url}
            </a>
          </div>
          <div className="source-badges">
            <span className="status">Inactivo</span>
            <span className="status active">{modeLabel(source.monitor_mode)}</span>
          </div>
        </div>

        <div className="source-config-summary inactive">
          {draftSummary(source, sourceDraft, selectedFilterIds, selectedProxy, filterRules, proxyProfiles).map((entry) => (
            <span key={entry}>{entry}</span>
          ))}
        </div>
      </div>

      <details className="inactive-monitor-details">
        <summary>Editar configuracion</summary>
        <div className="source-schedule compact">
          <label>
            Modo
            <select value={sourceDraft.monitorMode} onChange={(event) => updateSourceDraft(source.id, 'monitorMode', event.target.value)}>
              <option value="manual">Puntual</option>
              <option value="continuous">Continuo</option>
              <option value="duration">Durante X minutos</option>
              <option value="window">Rango horario</option>
            </select>
          </label>
          {isRecurring ? (
            <>
              <label>
                Intervalo
                <input
                  type="number"
                  min="60"
                  max="3600"
                  value={sourceDraft.intervalSeconds}
                  onChange={(event) => updateSourceDraft(source.id, 'intervalSeconds', event.target.value)}
                />
              </label>
              <label>
                Jitter
                <input
                  type="number"
                  min="0"
                  max="50"
                  value={sourceDraft.jitterPercent}
                  onChange={(event) => updateSourceDraft(source.id, 'jitterPercent', event.target.value)}
                />
              </label>
            </>
          ) : null}
          {sourceDraft.monitorMode === 'window' ? (
            <>
              <label>
                Inicio
                <input
                  type="time"
                  value={sourceDraft.windowStart}
                  onChange={(event) => updateSourceDraft(source.id, 'windowStart', event.target.value)}
                />
              </label>
              <label>
                Fin
                <input
                  type="time"
                  value={sourceDraft.windowEnd}
                  onChange={(event) => updateSourceDraft(source.id, 'windowEnd', event.target.value)}
                />
              </label>
            </>
          ) : null}
          {sourceDraft.monitorMode === 'duration' ? (
            <label>
              Minutos
              <input
                type="number"
                min="1"
                max="1440"
                value={sourceDraft.sessionDurationMinutes}
                onChange={(event) => updateSourceDraft(source.id, 'sessionDurationMinutes', event.target.value)}
              />
            </label>
          ) : null}
          <label>
            Proxy
            <select value={selectedProxy} onChange={(event) => updateSourceProxy(source.id, event.target.value)}>
              <option value="">Directo / .env</option>
              {proxyProfiles.map((proxy) => (
                <option key={proxy.id} value={proxy.id}>
                  {proxy.name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" disabled={savingSourceId === source.id} title="Guardar monitor" onClick={() => onSaveSourceSchedule(source)}>
            <Save size={16} />
            Guardar
          </button>
        </div>

        <div className="source-filter-picker compact">
          {filterRules.length === 0 ? (
            <span>Sin filtros: las oportunidades se marcaran como Sin filtros.</span>
          ) : (
            filterRules.map((rule) => (
              <label key={rule.id}>
                <input type="checkbox" checked={selectedFilterIds.includes(rule.id)} onChange={() => toggleSourceFilter(source.id, rule.id)} />
                {rule.name}
              </label>
            ))
          )}
        </div>
      </details>

      {source.last_run_at ? <p className="source-session-line">Ultima consulta {formatDate(source.last_run_at)}</p> : null}

      <div className="source-actions">
        <button type="button" disabled={runningSessionId !== null} onClick={() => onStartSession(source)}>
          <Play size={17} />
          {sourceDraft.monitorMode === 'manual' ? 'Ejecutar prueba' : 'Activar monitor'}
        </button>
        <button
          type="button"
          disabled={savingSourceId === source.id}
          title="Archivar monitor"
          onClick={() => {
            if (window.confirm(`Archivar el monitor "${source.name}"? Se conservara el historico.`)) {
              onDeleteSource(source);
            }
          }}
        >
          <Trash2 size={16} />
          Archivar monitor
        </button>
      </div>
    </article>
  );
}

function monitorSummary(source: SearchSource, filterRules: FilterRule[], proxyProfiles: ProxyProfile[]): string[] {
  const config = source.scheduler_config ?? {};
  const entries = [`Modo: ${modeLabel(source.monitor_mode)}`];
  if (source.monitor_mode !== 'manual') {
    entries.push(`Cada ${config.interval_seconds ?? 300}s`);
    entries.push(`Jitter ${config.jitter_percent ?? 20}%`);
  }
  if (source.monitor_mode === 'duration' && source.monitor_until) {
    entries.push(`Hasta ${formatDate(source.monitor_until)}`);
  }
  if (source.monitor_mode === 'window' && config.allowed_windows?.[0]) {
    entries.push(`Ventana ${config.allowed_windows[0]}`);
  }
  entries.push(`Filtros: ${filterLabel(source.filter_rule_ids, filterRules)}`);
  entries.push(`Proxy: ${proxyLabel(source.proxy_profile_id, proxyProfiles)}`);
  if (source.monitor_started_at) {
    entries.push(`Activo desde ${formatDate(source.monitor_started_at)}`);
  }
  if (source.last_run_at) {
    entries.push(`Ultima ${formatDate(source.last_run_at)}`);
  }
  if (source.next_run_at) {
    entries.push(`Proxima ${formatDate(source.next_run_at)}`);
  }
  return entries;
}

function draftSummary(
  source: SearchSource,
  draft: SourceDraft,
  selectedFilterIds: number[],
  selectedProxy: string,
  filterRules: FilterRule[],
  proxyProfiles: ProxyProfile[]
): string[] {
  const entries = [`Modo: ${modeLabel(draft.monitorMode)}`];
  if (draft.monitorMode !== 'manual') {
    entries.push(`Cada ${draft.intervalSeconds || source.scheduler_config.interval_seconds || 300}s`);
    entries.push(`Jitter ${draft.jitterPercent || source.scheduler_config.jitter_percent || 20}%`);
  }
  if (draft.monitorMode === 'duration') {
    entries.push(`${draft.sessionDurationMinutes || source.duration_minutes || 60} min`);
  }
  if (draft.monitorMode === 'window' && draft.windowStart && draft.windowEnd) {
    entries.push(`${draft.windowStart}-${draft.windowEnd}`);
  }
  entries.push(`Filtros: ${filterLabel(selectedFilterIds, filterRules)}`);
  entries.push(`Proxy: ${selectedProxy ? proxyLabel(Number(selectedProxy), proxyProfiles) : 'Directo / .env'}`);
  if (source.last_run_at) {
    entries.push(`Ultima ${formatDate(source.last_run_at)}`);
  }
  return entries;
}

function shouldRefreshRuns(phase: string): boolean {
  return phase === 'run_started' || phase === 'run_succeeded' || phase === 'run_failed';
}

function filterLabel(filterIds: number[], filterRules: FilterRule[]): string {
  if (filterIds.length === 0) {
    return 'sin filtros';
  }
  const names = filterIds.map((filterId) => filterRules.find((rule) => rule.id === filterId)?.name ?? `#${filterId}`);
  return names.join(', ');
}

function proxyLabel(proxyProfileId: number | null, proxyProfiles: ProxyProfile[]): string {
  if (!proxyProfileId) {
    return 'Directo / .env';
  }
  return proxyProfiles.find((proxy) => proxy.id === proxyProfileId)?.name ?? `Perfil #${proxyProfileId}`;
}

function modeLabel(mode: SearchSource['monitor_mode']): string {
  if (mode === 'continuous') {
    return 'Continuo';
  }
  if (mode === 'duration') {
    return 'Duracion';
  }
  if (mode === 'window') {
    return 'Rango horario';
  }
  return 'Puntual';
}
