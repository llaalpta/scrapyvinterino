import { Play, Power, Save } from 'lucide-react';
import type { FormEvent } from 'react';
import type { SearchSource } from '../../api';
import { buildSourceDraft, type SourceDraft } from './sourceDrafts';

export function SourcesView({
  onCreateSource,
  onRunSource,
  onSaveSourceSchedule,
  onToggleSource,
  runningSourceId,
  savingSourceId,
  sourceDrafts,
  sourceName,
  sources,
  sourceUrl,
  setSourceName,
  setSourceUrl,
  updateSourceDraft
}: {
  onCreateSource: (event: FormEvent<HTMLFormElement>) => void;
  onRunSource: (sourceId: number) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onToggleSource: (source: SearchSource) => void;
  runningSourceId: number | null;
  savingSourceId: number | null;
  sourceDrafts: Record<number, SourceDraft>;
  sourceName: string;
  sources: SearchSource[];
  sourceUrl: string;
  setSourceName: (value: string) => void;
  setSourceUrl: (value: string) => void;
  updateSourceDraft: (sourceId: number, field: keyof SourceDraft, value: string) => void;
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
        <div className="sources-list">
          {sources.map((source) => (
            <article className="source-row" key={source.id}>
              <div className="source-main">
                <strong>{source.name}</strong>
                <a href={source.url} target="_blank" rel="noreferrer">
                  {source.url}
                </a>
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
                <button type="button" disabled={savingSourceId === source.id} title="Guardar cadencia" onClick={() => onSaveSourceSchedule(source)}>
                  <Save size={16} />
                  Guardar
                </button>
              </div>
              <button
                type="button"
                disabled={!source.is_active || runningSourceId !== null}
                title={source.is_active ? 'Ejecutar esta fuente' : 'La fuente esta pausada'}
                onClick={() => onRunSource(source.id)}
              >
                <Play size={17} />
                {runningSourceId === source.id ? 'Ejecutando' : 'Ejecutar'}
              </button>
              <button
                type="button"
                disabled={savingSourceId === source.id}
                title={source.is_active ? 'Pausar fuente' : 'Activar fuente'}
                onClick={() => onToggleSource(source)}
              >
                <Power size={16} />
                {source.is_active ? 'Pausar' : 'Activar'}
              </button>
              <span className={source.is_active ? 'status active' : 'status'}>{source.is_active ? 'Activa' : 'Pausada'}</span>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
