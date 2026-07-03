import { Play, Power, Save } from 'lucide-react';
import type { FormEvent } from 'react';
import type { ProxyProfile, SchedulerState } from '../../api';

export function SettingsView({
  onCreateProxy,
  onTestProxy,
  onToggleScheduler,
  proxyDraft,
  proxyProfiles,
  savingProxy,
  savingScheduler,
  scheduler,
  setProxyDraft
}: {
  onCreateProxy: (event: FormEvent<HTMLFormElement>) => void;
  onTestProxy: (profileId: number) => void;
  onToggleScheduler: () => void;
  proxyDraft: ProxyDraft;
  proxyProfiles: ProxyProfile[];
  savingProxy: boolean;
  savingScheduler: boolean;
  scheduler: SchedulerState | null;
  setProxyDraft: (draft: ProxyDraft) => void;
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
              {scheduler.max_concurrent_runs} global / {scheduler.per_source_concurrency} por sesion
            </p>
          </div>
          <div>
            <strong>Zona horaria</strong>
            <p>{scheduler.timezone}</p>
          </div>
          <div>
            <strong>Proxy .env</strong>
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

      <div className="proxy-section">
        <div className="panel-heading nested">
          <h3>Proxy pool</h3>
          <span>{proxyProfiles.length}</span>
        </div>
        <form className="proxy-form" onSubmit={onCreateProxy}>
          <input value={proxyDraft.name} onChange={(event) => setProxyDraft({ ...proxyDraft, name: event.target.value })} placeholder="Nombre" required />
          <select value={proxyDraft.scheme} onChange={(event) => setProxyDraft({ ...proxyDraft, scheme: event.target.value })}>
            <option value="http">http</option>
            <option value="https">https</option>
            <option value="socks5">socks5</option>
          </select>
          <input value={proxyDraft.host} onChange={(event) => setProxyDraft({ ...proxyDraft, host: event.target.value })} placeholder="Host" required />
          <input
            value={proxyDraft.port}
            type="number"
            min="1"
            max="65535"
            onChange={(event) => setProxyDraft({ ...proxyDraft, port: event.target.value })}
            placeholder="Puerto"
            required
          />
          <input value={proxyDraft.username} onChange={(event) => setProxyDraft({ ...proxyDraft, username: event.target.value })} placeholder="Usuario" />
          <input
            value={proxyDraft.password}
            type="password"
            onChange={(event) => setProxyDraft({ ...proxyDraft, password: event.target.value })}
            placeholder="Password"
          />
          <button type="submit" disabled={savingProxy}>
            <Save size={16} />
            Guardar proxy
          </button>
        </form>
        {proxyProfiles.length === 0 ? (
          <p className="empty-inline">Sin proxys configurados. Las sesiones pueden usar directo o el proxy de .env.</p>
        ) : (
          <div className="proxy-list">
            {proxyProfiles.map((proxy) => (
              <article className="proxy-row" key={proxy.id}>
                <div>
                  <strong>{proxy.name}</strong>
                  <span>
                    {proxy.scheme}://{proxy.username_masked ? `${proxy.username_masked}@` : ''}
                    {proxy.host}:{proxy.port}
                  </span>
                </div>
                <span className={proxy.is_active ? 'status active' : 'status'}>{proxy.is_active ? 'Activo' : 'Pausado'}</span>
                <span className="status">{proxy.last_test_ip ?? proxy.last_test_status ?? 'Sin test'}</span>
                <button type="button" onClick={() => onTestProxy(proxy.id)}>
                  <Play size={16} />
                  Test
                </button>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export type ProxyDraft = {
  name: string;
  scheme: string;
  host: string;
  port: string;
  username: string;
  password: string;
};
