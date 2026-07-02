import { ExternalLink, Heart, Play, Settings, ShoppingCart } from 'lucide-react';
import { FormEvent, useEffect, useState } from 'react';
import {
  createSource,
  fetchItems,
  fetchRuns,
  fetchSources,
  runSource,
  type Item,
  type Run,
  type SearchSource
} from './api';

const navItems = [
  { id: 'opportunities', label: 'Articulos' },
  { id: 'sources', label: 'Busquedas' },
  { id: 'filters', label: 'Filtros' },
  { id: 'runs', label: 'Runs' },
  { id: 'settings', label: 'Settings' }
];

export function App() {
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runningSourceId, setRunningSourceId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [activeSection, setActiveSection] = useState('opportunities');
  const activeSource = sources.find((source) => source.is_active);

  useEffect(() => {
    Promise.all([fetchSources(), fetchItems(), fetchRuns()])
      .then(([sourceData, itemData, runData]) => {
        setSources(sourceData);
        setItems(itemData);
        setRuns(runData);
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : 'Error cargando datos');
      });
  }, []);

  async function onCreateSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const created = await createSource({ name: sourceName, url: sourceUrl });
      setSources((current) => [created, ...current]);
      setSourceName('');
      setSourceUrl('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo crear la fuente');
    }
  }

  async function onRunSource(sourceId: number) {
    setError(null);
    setRunningSourceId(sourceId);
    try {
      const created = await runSource(sourceId);
      const itemData = await fetchItems();
      setRuns((current) => [created, ...current.filter((run) => run.id !== created.id)].slice(0, 50));
      setItems(itemData);
      setActiveSection('runs');
      document.getElementById('runs')?.scrollIntoView({ block: 'start' });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo ejecutar la busqueda');
    } finally {
      setRunningSourceId(null);
    }
  }

  function getSourceName(sourceId: number): string {
    return sources.find((source) => source.id === sourceId)?.name ?? `Fuente ${sourceId}`;
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">Personal dashboard</p>
          <h1>Vinted Monitor</h1>
        </div>
        <nav>
          {navItems.map((item) => (
            <a
              className={activeSection === item.id ? 'active' : ''}
              href={`#${item.id}`}
              key={item.id}
              onClick={() => setActiveSection(item.id)}
            >
              {item.label}
            </a>
          ))}
        </nav>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <h2>Articulos guardados</h2>
            <p>{items.length} articulos persistidos desde {sources.length} fuentes</p>
          </div>
          <button
            type="button"
            disabled={!activeSource || runningSourceId !== null}
            title={activeSource ? 'Ejecutar fuente activa' : 'Crea una fuente activa para ejecutar una busqueda'}
            onClick={() => {
              if (activeSource) {
                void onRunSource(activeSource.id);
              }
            }}
          >
            <Play size={18} />
            {runningSourceId ? 'Ejecutando...' : 'Ejecutar busqueda'}
          </button>
        </header>

        {error ? <div className="notice">{error}</div> : null}

        <section id="sources" className="sources-panel">
          <div className="panel-heading">
            <h3>Fuentes de busqueda</h3>
            <span>{sources.length}</span>
          </div>
          <form className="source-form" onSubmit={onCreateSource}>
            <input
              value={sourceName}
              onChange={(event) => setSourceName(event.target.value)}
              placeholder="Nombre de busqueda"
              required
            />
            <input
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="URL de catalogo Vinted"
              required
            />
            <button type="submit">Guardar URL</button>
          </form>
          {sources.length === 0 ? (
            <p className="empty-inline">No hay fuentes configuradas.</p>
          ) : (
            <div className="sources-list">
              {sources.map((source) => (
                <article className="source-row" key={source.id}>
                  <div>
                    <strong>{source.name}</strong>
                    <a href={source.url} target="_blank" rel="noreferrer">{source.url}</a>
                  </div>
                  <button
                    type="button"
                    disabled={!source.is_active || runningSourceId !== null}
                    title={source.is_active ? 'Ejecutar esta fuente' : 'La fuente esta pausada'}
                    onClick={() => void onRunSource(source.id)}
                  >
                    <Play size={17} />
                    {runningSourceId === source.id ? 'Ejecutando' : 'Ejecutar'}
                  </button>
                  <span className={source.is_active ? 'status active' : 'status'}>{source.is_active ? 'Activa' : 'Pausada'}</span>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="toolbar" aria-label="Acciones principales">
          <a className="button-link" href="#filters" onClick={() => setActiveSection('filters')}><Settings size={18} /> Filtros</a>
          <button type="button" disabled title="Disponible cuando implementemos acciones autenticadas"><Heart size={18} /> Favoritos</button>
          <button type="button" disabled title="Disponible cuando implementemos precompra"><ShoppingCart size={18} /> Compra manual</button>
        </section>

        <section id="opportunities" className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Articulo</th>
                <th>Marca</th>
                <th>Talla</th>
                <th>Estado</th>
                <th>Precio</th>
                <th>Favs</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="empty">
                    Todavia no hay articulos guardados. Ejecuta una fuente para persistir resultados.
                  </td>
                </tr>
              ) : (
                items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="item-cell">
                        {item.image_url ? <img src={item.image_url} alt="" /> : <div className="thumb" />}
                        <span>{item.title}</span>
                      </div>
                    </td>
                    <td>{item.brand ?? '-'}</td>
                    <td>{item.size ?? '-'}</td>
                    <td>{item.status ?? '-'}</td>
                    <td>{item.price_amount ? `${item.price_amount} ${item.currency ?? ''}` : '-'}</td>
                    <td>{item.favorite_count ?? '-'}</td>
                    <td>
                      <div className="row-actions">
                        <a href={item.url} target="_blank" rel="noreferrer" title="Ver en Vinted">
                          <ExternalLink size={17} />
                        </a>
                        <button type="button" title="Marcar favorito" disabled>
                          <Heart size={17} />
                        </button>
                        <button type="button" title="Comprar" disabled>
                          <ShoppingCart size={17} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>

        <section id="filters" className="section-panel">
          <div className="panel-heading">
            <h3>Filtros</h3>
            <span>0</span>
          </div>
          <p className="empty-inline">Sin filtros configurados.</p>
        </section>

        <section id="runs" className="section-panel">
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

        <section id="settings" className="section-panel">
          <div className="panel-heading">
            <h3>Settings</h3>
            <span>Local</span>
          </div>
          <p className="empty-inline">Configuracion pendiente.</p>
        </section>
      </section>
    </main>
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('es-ES', {
    dateStyle: 'short',
    timeStyle: 'medium'
  }).format(new Date(value));
}
