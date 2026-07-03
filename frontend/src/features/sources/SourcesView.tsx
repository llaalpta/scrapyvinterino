import { Play, Power, Save, Square } from 'lucide-react';
import type { FormEvent } from 'react';
import type { FilterRule, MonitorSession, ProxyProfile, SearchSource } from '../../api';
import { formatDate } from '../../utils/format';
import { buildSourceDraft, type SourceDraft } from './sourceDrafts';

export function SourcesView({
  filterRules,
  monitorSessions,
  onCreateSource,
  onRunSession,
  onSaveSourceSchedule,
  onStartSession,
  onStopSession,
  onToggleSource,
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
  monitorSessions: MonitorSession[];
  onCreateSource: (event: FormEvent<HTMLFormElement>) => void;
  onRunSession: (sessionId: number) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  onStopSession: (sessionId: number) => void;
  onToggleSource: (source: SearchSource) => void;
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
        <h3>Fuentes de busqueda</h3>
        <span>{sources.length}</span>
      </div>
      <form className="source-form" onSubmit={onCreateSource}>
        <input value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="Nombre de busqueda" required />
        <input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="URL de catalogo Vinted" required />
        <button type="submit">Guardar URL</button>
      </form>
      {sources.length === 0 ? (
        <p className="empty-inline">No hay fuentes configuradas.</p>
      ) : (
        <div className="source-cards">
          {sources.map((source) => {
            const activeSession = monitorSessions.find((session) => session.source_id === source.id && session.status === 'active');
            const selectedFilters = selectedFilterIdsBySource[source.id] ?? [];
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
                    <span className={source.is_active ? 'status active' : 'status'}>{source.is_active ? 'Disponible' : 'Pausada'}</span>
                    {activeSession ? <span className="status running">Sesion activa</span> : null}
                  </div>
                </div>

                <div className="source-schedule">
                  <label>
                    Intervalo
                    <input
                      type="number"
                      min="60"
                      max="3600"
                      value={(sourceDrafts[source.id] ?? buildSourceDraft(source)).intervalSeconds}
                      onChange={(event) => updateSourceDraft(source.id, 'intervalSeconds', event.target.value)}
                    />
                  </label>
                  <label>
                    Jitter %
                    <input
                      type="number"
                      min="0"
                      max="50"
                      value={(sourceDrafts[source.id] ?? buildSourceDraft(source)).jitterPercent}
                      onChange={(event) => updateSourceDraft(source.id, 'jitterPercent', event.target.value)}
                    />
                  </label>
                  <label>
                    Ventanas
                    <input
                      value={(sourceDrafts[source.id] ?? buildSourceDraft(source)).allowedWindows}
                      placeholder="09:00-23:00"
                      onChange={(event) => updateSourceDraft(source.id, 'allowedWindows', event.target.value)}
                    />
                  </label>
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
                  <button type="button" disabled={savingSourceId === source.id} title="Guardar cadencia" onClick={() => onSaveSourceSchedule(source)}>
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

                {activeSession ? (
                  <p className="source-session-line">
                    Sesion #{activeSession.id} desde {formatDate(activeSession.started_at)}
                    {activeSession.proxy_name ? ` - ${activeSession.proxy_name}` : ''}
                  </p>
                ) : null}

                <div className="source-actions">
                  {activeSession ? (
                    <>
                      <button type="button" disabled={runningSessionId !== null} onClick={() => onRunSession(activeSession.id)}>
                        <Play size={17} />
                        {runningSessionId === activeSession.id ? 'Ejecutando' : 'Ejecutar sesion'}
                      </button>
                      <button type="button" onClick={() => onStopSession(activeSession.id)}>
                        <Square size={16} />
                        Detener sesion
                      </button>
                    </>
                  ) : (
                    <button type="button" disabled={!source.is_active} onClick={() => onStartSession(source)}>
                      <Play size={17} />
                      Lanzar sesion
                    </button>
                  )}
                  <button type="button" disabled={savingSourceId === source.id} onClick={() => onToggleSource(source)}>
                    <Power size={16} />
                    {source.is_active ? 'Pausar fuente' : 'Activar fuente'}
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
