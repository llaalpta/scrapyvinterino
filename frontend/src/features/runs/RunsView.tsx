import type { Run } from '../../api';
import { formatDate } from '../../utils/format';

export function RunsView({ getSourceName, runs }: { getSourceName: (sourceId: number) => string; runs: Run[] }) {
  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Runs</h3>
        <span>{runs.length}</span>
      </div>
      {runs.length === 0 ? (
        <p className="empty-inline">Sin ejecuciones registradas.</p>
      ) : (
        <div className="runs-list">
          {runs.map((run) => (
            <article className="run-row" key={run.id}>
              <div>
                <strong>{getSourceName(run.source_id)}</strong>
                <span>{formatDate(run.started_at)}</span>
                {run.error_message ? <p>{run.error_message}</p> : null}
              </div>
              <dl>
                <div>
                  <dt>Estado</dt>
                  <dd className={`run-status ${run.status}`}>{run.status}</dd>
                </div>
                <div>
                  <dt>Trigger</dt>
                  <dd>{run.trigger}</dd>
                </div>
                <div>
                  <dt>Encontrados</dt>
                  <dd>{run.items_found}</dd>
                </div>
                <div>
                  <dt>Nuevos</dt>
                  <dd>{run.items_new}</dd>
                </div>
                <div>
                  <dt>Oportunidades</dt>
                  <dd>{run.opportunities_created}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
