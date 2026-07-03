import { Activity, Bell, Database, Search, Settings, SlidersHorizontal } from 'lucide-react';

export const navItems = [
  { id: 'results', label: 'Resultados', icon: Search },
  { id: 'opportunities', label: 'Oportunidades', icon: Bell },
  { id: 'sources', label: 'Monitores', icon: Database },
  { id: 'filters', label: 'Filtros', icon: SlidersHorizontal },
  { id: 'runs', label: 'Actividad', icon: Activity },
  { id: 'settings', label: 'Ajustes', icon: Settings }
];
