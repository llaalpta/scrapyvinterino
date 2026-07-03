import { PanelLeftClose, PanelLeftOpen, Play } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SearchSource } from '../../api';
import { navItems } from '../../app/navigation';

export function DashboardShell({
  activeSection,
  activeSource,
  activeSubtitle,
  activeTitle,
  children,
  error,
  navCollapsed,
  runningSourceId,
  onRunSource,
  onSelectSection,
  onToggleNav
}: {
  activeSection: string;
  activeSource: SearchSource | undefined;
  activeSubtitle: string;
  activeTitle: string;
  children: ReactNode;
  error: string | null;
  navCollapsed: boolean;
  runningSourceId: number | null;
  onRunSource: (sourceId: number) => void;
  onSelectSection: (section: string) => void;
  onToggleNav: () => void;
}) {
  return (
    <main className={navCollapsed ? 'shell nav-collapsed' : 'shell'}>
      <aside className="sidebar">
        <div className="brand-row">
          <div className="brand-copy">
            <p className="eyebrow">Personal dashboard</p>
            <h1>Vinted Monitor</h1>
          </div>
          <button
            className="nav-toggle"
            type="button"
            aria-label={navCollapsed ? 'Expandir navegacion' : 'Contraer navegacion'}
            title={navCollapsed ? 'Expandir navegacion' : 'Contraer navegacion'}
            onClick={onToggleNav}
          >
            {navCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>
        </div>
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={activeSection === item.id ? 'active' : ''}
                key={item.id}
                type="button"
                title={navCollapsed ? item.label : undefined}
                aria-label={item.label}
                onClick={() => onSelectSection(item.id)}
              >
                <Icon size={18} />
                <span className="nav-label">{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <h2>{activeTitle}</h2>
            <p>{activeSubtitle}</p>
          </div>
          <button
            type="button"
            disabled={!activeSource || runningSourceId !== null}
            title={activeSource ? 'Ejecutar fuente activa' : 'Crea una fuente activa para ejecutar una busqueda'}
            onClick={() => {
              if (activeSource) {
                onRunSource(activeSource.id);
              }
            }}
          >
            <Play size={18} />
            {runningSourceId ? 'Ejecutando...' : 'Ejecutar busqueda'}
          </button>
        </header>

        {error ? <div className="notice">{error}</div> : null}
        {children}
      </section>
    </main>
  );
}
