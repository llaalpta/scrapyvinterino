import { Save } from 'lucide-react';
import type { FormEvent } from 'react';
import type { FilterRule } from '../../api';

export function FiltersView({
  filterName,
  filterTerms,
  filterRules,
  saving,
  onCreateFilter,
  setFilterName,
  setFilterTerms
}: {
  filterName: string;
  filterTerms: string;
  filterRules: FilterRule[];
  saving: boolean;
  onCreateFilter: (event: FormEvent<HTMLFormElement>) => void;
  setFilterName: (value: string) => void;
  setFilterTerms: (value: string) => void;
}) {
  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Filtros excluyentes</h3>
        <span>{filterRules.length}</span>
      </div>
      <form className="source-form" onSubmit={onCreateFilter}>
        <input value={filterName} onChange={(event) => setFilterName(event.target.value)} placeholder="Nombre del filtro" required />
        <input
          value={filterTerms}
          onChange={(event) => setFilterTerms(event.target.value)}
          placeholder="manchas, roto, destenido"
          required
        />
        <button type="submit" disabled={saving}>
          <Save size={16} />
          Guardar filtro
        </button>
      </form>
      {filterRules.length === 0 ? (
        <p className="empty-inline">Sin filtros. Las sesiones sin filtros crean oportunidades marcadas como Sin filtros.</p>
      ) : (
        <div className="filters-list">
          {filterRules.map((rule) => (
            <article className="filter-rule-row" key={rule.id}>
              <strong>{rule.name}</strong>
              <span>{rule.definition.blacklist_terms?.join(', ') || 'Sin terminos'}</span>
              <span className={rule.is_active ? 'status active' : 'status'}>{rule.is_active ? 'Activo' : 'Pausado'}</span>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
