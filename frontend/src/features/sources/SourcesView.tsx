import { Play, Save, Square, Trash2 } from 'lucide-react';
import type { FormEvent } from 'react';
import type { FilterRule, ProxyProfile, SearchSource } from '../../api';
import { formatDate } from '../../utils/format';
import { buildSourceDraft, type SourceDraft } from './sourceDrafts';

export function SourcesView({
  filterRules,
  onCreateSource,
  onDeleteSource,
  onRunMonitor,
  onSaveSourceSchedule,
  onStartSession,
  onStopMonitor,
  proxyProfiles,
  runningSessionId,
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
  onRunMonitor: (sourceId: number) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  onStopMonitor: (sourceId: number) => void;
  proxyProfiles: ProxyProfile[];
  runningSessionId: number | null;
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
      {sources.length === 0 ? (
        <p className="empty-inline">No hay monitores configurados.</p>
      ) : (
        <div className="source-cards">
          {sources.map((source) => {
            const selectedFilters = selectedFilterIdsBySource[source.id] ?? [];
            const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
            const isRecurring = draft.monitorMode !== 'manual';
            return (
              <article className="source-card" key={source.id}>
                <div className="source-card-header">
                  <div className="source-main">
                    <strong>{source.name}</strong>
                    <a href={source.url} target="_blank" rel="noreferrer">
                      {source.url}
                    </a>
                  </div>
                  <div className="source-badges">
                    <span className={source.is_active ? 'status running' : 'status'}>{source.is_active ? 'Activo' : 'Pausado'}</span>
                    <span className="status active">{modeLabel(source.monitor_mode)}</span>
                  </div>
                </div>

                <div className="source-schedule">
                  <label>
                    Modo
                    <select value={draft.monitorMode} onChange={(event) => updateSourceDraft(source.id, 'monitorMode', event.target.value)}>
                      <option value="manual">Puntual</option>
                      <option value="continuous">Continuo</option>
                      <option value="duration">Durante X minutos</option>
                      <option value="window">Rango horario</option>
                    </select>
                  </label>
                  {isRecurring ? (
                    <>
                  <label>
                    Intervalo seg
                    <input
                      type="number"
                      min="60"
                      max="3600"
                      value={draft.intervalSeconds}
                      onChange={(event) => updateSourceDraft(source.id, 'intervalSeconds', event.target.value)}
                    />
                  </label>
                  <label>
                    Jitter %
                    <input
                      type="number"
                      min="0"
                      max="50"
                      value={draft.jitterPercent}
                      onChange={(event) => updateSourceDraft(source.id, 'jitterPercent', event.target.value)}
                    />
                  </label>
                    </>
                  ) : null}
                  {draft.monitorMode === 'window' ? (
                    <>
                  <label>
                    Inicio
                    <input
                      type="time"
                      value={draft.windowStart}
                      onChange={(event) => updateSourceDraft(source.id, 'windowStart', event.target.value)}
                    />
                  </label>
                  <label>
                    Fin
                    <input
                      type="time"
                      value={draft.windowEnd}
                      onChange={(event) => updateSourceDraft(source.id, 'windowEnd', event.target.value)}
                    />
                  </label>
                    </>
                  ) : null}
                  <label>
                    Proxy
                    <select value={selectedProxyBySource[source.id] ?? ''} onChange={(event) => updateSourceProxy(source.id, event.target.value)}>
                      <option value="">Directo / .env</option>
                      {proxyProfiles.map((proxy) => (
                        <option key={proxy.id} value={proxy.id}>
                          {proxy.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {draft.monitorMode === 'duration' ? (
                  <label>
                    Duracion min
                    <input
                      type="number"
                      min="1"
                      max="1440"
                      value={draft.sessionDurationMinutes}
                      onChange={(event) => updateSourceDraft(source.id, 'sessionDurationMinutes', event.target.value)}
                    />
                  </label>
                  ) : null}
                  <button type="button" disabled={savingSourceId === source.id} title="Guardar monitor" onClick={() => onSaveSourceSchedule(source)}>
                    <Save size={16} />
                    Guardar
                  </button>
                </div>

                <div className="source-filter-picker">
                  {filterRules.length === 0 ? (
                    <span>Sin filtros: las oportunidades se marcaran como Sin filtros.</span>
                  ) : (
                    filterRules.map((rule) => (
                      <label key={rule.id}>
                        <input
                          type="checkbox"
                          checked={selectedFilters.includes(rule.id)}
                          onChange={() => toggleSourceFilter(source.id, rule.id)}
                        />
                        {rule.name}
                      </label>
                    ))
                  )}
                </div>

                {source.is_active || source.last_run_at ? (
                  <p className="source-session-line">
                    {source.is_active ? `Activo desde ${source.monitor_started_at ? formatDate(source.monitor_started_at) : 'ahora'}` : 'Pausado'}
                    {source.monitor_until ? ` hasta ${formatDate(source.monitor_until)}` : ''}
                    {source.last_run_at ? ` - ultima consulta ${formatDate(source.last_run_at)}` : ''}
                  </p>
                ) : null}

                <div className="source-actions">
                  {source.is_active ? (
                    <>
                      <button type="button" disabled={runningSessionId !== null} onClick={() => onRunMonitor(source.id)}>
                        <Play size={17} />
                        {runningSessionId === source.id ? 'Ejecutando' : 'Ejecutar ahora'}
                      </button>
                      <button type="button" onClick={() => onStopMonitor(source.id)}>
                        <Square size={16} />
                        Parar monitor
                      </button>
                    </>
                  ) : (
                    <button type="button" disabled={runningSessionId !== null} onClick={() => onStartSession(source)}>
                      <Play size={17} />
                      {draft.monitorMode === 'manual' ? 'Lanzar puntual' : 'Activar monitor'}
                    </button>
                  )}
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
          })}
        </div>
      )}
    </section>
  );
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
