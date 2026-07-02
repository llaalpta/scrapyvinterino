import { ExternalLink, Heart, Play, Settings, ShoppingCart } from 'lucide-react';
import { FormEvent, useEffect, useState } from 'react';
import { createSource, fetchItems, fetchSources, type Item, type SearchSource } from './api';

export function App() {
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');

  useEffect(() => {
    Promise.all([fetchSources(), fetchItems()])
      .then(([sourceData, itemData]) => {
        setSources(sourceData);
        setItems(itemData);
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

  return (
    <main className="shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">Personal dashboard</p>
          <h1>Vinted Monitor</h1>
        </div>
        <nav>
          <a className="active" href="#opportunities">Oportunidades</a>
          <a href="#sources">Busquedas</a>
          <a href="#filters">Filtros</a>
          <a href="#runs">Runs</a>
          <a href="#settings">Settings</a>
        </nav>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <h2>Oportunidades nuevas</h2>
            <p>{sources.length} fuentes configuradas</p>
          </div>
          <button type="button">
            <Play size={18} />
            Ejecutar busqueda
          </button>
        </header>

        {error ? <div className="notice">{error}</div> : null}

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

        <section className="toolbar" aria-label="Acciones principales">
          <button type="button"><Settings size={18} /> Filtros</button>
          <button type="button"><Heart size={18} /> Favoritos</button>
          <button type="button"><ShoppingCart size={18} /> Compra manual</button>
        </section>

        <section className="table-wrap">
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
                    Todavia no hay articulos guardados.
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
      </section>
    </main>
  );
}
