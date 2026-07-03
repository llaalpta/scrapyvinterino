import { Power } from 'lucide-react';
import type { SchedulerState } from '../../api';

export function SettingsView({
  onToggleScheduler,
  savingScheduler,
  scheduler
}: {
  onToggleScheduler: () => void;
  savingScheduler: boolean;
  scheduler: SchedulerState | null;
}) {
  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Settings</h3>
        <span>{scheduler?.effective_enabled ? 'Scheduler activo' : 'Scheduler parado'}</span>
      </div>
      {scheduler ? (
        <div className="settings-grid">
          <div>
            <strong>Scheduler</strong>
            <p>{scheduler.enabled ? 'Habilitado en la UI' : 'Deshabilitado en la UI'}</p>
          </div>
          <div>
            <strong>Runtime</strong>
            <p>{scheduler.runtime_enabled ? 'Permitido por .env' : 'Bloqueado por .env'}</p>
          </div>
          <div>
            <strong>Concurrencia</strong>
            <p>
              {scheduler.max_concurrent_runs} global / {scheduler.per_source_concurrency} por fuente
            </p>
          </div>
          <div>
            <strong>Zona horaria</strong>
            <p>{scheduler.timezone}</p>
          </div>
          <div>
            <strong>Proxy Vinted</strong>
            <p>{scheduler.proxy_enabled ? (scheduler.proxy_configured ? 'Activo y configurado' : 'Activo sin URL') : 'Desactivado'}</p>
          </div>
          <button type="button" disabled={savingScheduler} onClick={onToggleScheduler}>
            <Power size={17} />
            {scheduler.enabled ? 'Deshabilitar scheduler' : 'Habilitar scheduler'}
          </button>
        </div>
      ) : (
        <p className="empty-inline">No se pudo cargar la configuracion del scheduler.</p>
      )}
    </section>
  );
}
