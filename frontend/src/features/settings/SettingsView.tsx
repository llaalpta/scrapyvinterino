import { Pause, Play, Power, Save } from 'lucide-react';
import type { FormEvent } from 'react';
import type { ProxyProfile, SchedulerState, SchedulerUpdate } from '../../api';

export function SettingsView({
  onCreateProxy,
  onTestProxy,
  onToggleProxy,
  onToggleScheduler,
  onUpdateSchedulerConfig,
  proxyDraft,
  proxyProfiles,
  savingProxy,
  savingScheduler,
  scheduler,
  setProxyDraft
}: {
  onCreateProxy: (event: FormEvent<HTMLFormElement>) => void;
  onTestProxy: (profileId: number) => void;
  onToggleProxy: (profile: ProxyProfile) => void;
  onToggleScheduler: () => void;
  onUpdateSchedulerConfig: (payload: SchedulerUpdate) => void;
  proxyDraft: ProxyDraft;
  proxyProfiles: ProxyProfile[];
  savingProxy: boolean;
  savingScheduler: boolean;
  scheduler: SchedulerState | null;
  setProxyDraft: (draft: ProxyDraft) => void;
}) {
  const updateNumber = (field: keyof SchedulerUpdate, value: string) => {
    if (!value) {
      return;
    }
    onUpdateSchedulerConfig({ [field]: Number(value) });
  };

  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Settings</h3>
        <span>{scheduler?.effective_enabled ? 'Scheduler activo' : 'Scheduler parado'}</span>
      </div>
      {scheduler ? (
        <div className="settings-grid scheduler-settings-grid">
          <div>
            <strong>Scheduler</strong>
            <p>{scheduler.enabled ? 'Habilitado en la UI' : 'Deshabilitado en la UI'}</p>
          </div>
          <div>
            <strong>Runtime</strong>
            <p>{scheduler.runtime_enabled ? 'Permitido por .env' : 'Bloqueado por .env'}</p>
          </div>
          <div>
            <strong>Capacidad</strong>
            <p>
              {scheduler.active_periodic_monitors}/{scheduler.effective_capacity} monitores activos
            </p>
          </div>
          <div>
            <strong>Egress</strong>
            <p>
              {scheduler.proxy_capacity} proxy / {scheduler.direct_capacity} directo
            </p>
          </div>
          <button type="button" disabled={savingScheduler} onClick={onToggleScheduler}>
            <Power size={17} />
            {scheduler.enabled ? 'Deshabilitar scheduler' : 'Habilitar scheduler'}
          </button>
          <label>
            Concurrencia global
            <input
              key={`global-${scheduler.max_concurrent_runs}`}
              type="number"
              min="1"
              max="20"
              defaultValue={scheduler.max_concurrent_runs}
              onBlur={(event) => updateNumber('max_concurrent_runs', event.target.value)}
            />
          </label>
          <label>
            Max por proxy
            <input
              key={`proxy-${scheduler.max_runs_per_proxy}`}
              type="number"
              min="1"
              max="10"
              defaultValue={scheduler.max_runs_per_proxy}
              onBlur={(event) => updateNumber('max_runs_per_proxy', event.target.value)}
            />
          </label>
          <label>
            Directos max
            <input
              key={`direct-${scheduler.direct_max_concurrent_runs}`}
              type="number"
              min="0"
              max="10"
              defaultValue={scheduler.direct_max_concurrent_runs}
              disabled={!scheduler.allow_direct_without_proxy}
              onBlur={(event) => updateNumber('direct_max_concurrent_runs', event.target.value)}
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={scheduler.allow_direct_without_proxy}
              onChange={(event) => onUpdateSchedulerConfig({ allow_direct_without_proxy: event.target.checked })}
            />
            Permitir directo sin proxy
          </label>
          <label>
            Resultados catalogo
            <input
              key={`catalog-${scheduler.catalog_per_page}`}
              type="number"
              min="1"
              max="96"
              defaultValue={scheduler.catalog_per_page}
              onBlur={(event) => updateNumber('catalog_per_page', event.target.value)}
            />
          </label>
          <label>
            Detalles por run
            <input
              key={`detail-${scheduler.detail_max_candidates_per_run}`}
              type="number"
              min="0"
              max="96"
              defaultValue={scheduler.detail_max_candidates_per_run}
              onBlur={(event) => updateNumber('detail_max_candidates_per_run', event.target.value)}
            />
          </label>
          <label>
            Timeout ms
            <input
              key={`timeout-${scheduler.request_timeout_ms}`}
              type="number"
              min="1000"
              max="60000"
              step="1000"
              defaultValue={scheduler.request_timeout_ms}
              onBlur={(event) => updateNumber('request_timeout_ms', event.target.value)}
            />
          </label>
          <label>
            Reintentos
            <input
              key={`retries-${scheduler.request_retries}`}
              type="number"
              min="0"
              max="5"
              defaultValue={scheduler.request_retries}
              onBlur={(event) => updateNumber('request_retries', event.target.value)}
            />
          </label>
          <label>
            Fallos antes de parar
            <input
              key={`failures-${scheduler.stop_monitor_after_consecutive_failures}`}
              type="number"
              min="1"
              max="20"
              defaultValue={scheduler.stop_monitor_after_consecutive_failures}
              onBlur={(event) => updateNumber('stop_monitor_after_consecutive_failures', event.target.value)}
            />
          </label>
          <label>
            Cooldown proxy min
            <input
              key={`cooldown-${scheduler.proxy_cooldown_minutes}`}
              type="number"
              min="1"
              max="1440"
              defaultValue={scheduler.proxy_cooldown_minutes}
              onBlur={(event) => updateNumber('proxy_cooldown_minutes', event.target.value)}
            />
          </label>
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
          <select value={proxyDraft.kind} onChange={(event) => setProxyDraft({ ...proxyDraft, kind: event.target.value as ProxyDraft['kind'] })}>
            <option value="own">Own</option>
            <option value="datacenter">Datacenter</option>
            <option value="residential">Residential</option>
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
          <input
            value={proxyDraft.maxConcurrentRuns}
            type="number"
            min="1"
            max="10"
            onChange={(event) => setProxyDraft({ ...proxyDraft, maxConcurrentRuns: event.target.value })}
            placeholder="Max runs"
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
          <p className="empty-inline">Sin proxys configurados. Se usara directo solo si la configuracion global lo permite.</p>
        ) : (
          <div className="proxy-list">
            {proxyProfiles.map((proxy) => (
              <article className="proxy-row" key={proxy.id}>
                <div>
                  <strong>{proxy.name}</strong>
                  <span>
                    {proxy.kind} · {proxy.scheme}://{proxy.username_masked ? `${proxy.username_masked}@` : ''}
                    {proxy.host}:{proxy.port} · max {proxy.max_concurrent_runs}
                  </span>
                </div>
                <span className={proxy.is_active ? 'status active' : 'status'}>{proxy.is_active ? 'Activo' : 'Pausado'}</span>
                <span className="status">{proxy.cooldown_until ? 'Cooldown' : proxy.last_test_ip ?? proxy.last_test_status ?? 'Sin test'}</span>
                <button type="button" onClick={() => onToggleProxy(proxy)}>
                  {proxy.is_active ? <Pause size={16} /> : <Play size={16} />}
                  {proxy.is_active ? 'Pausar' : 'Activar'}
                </button>
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
  kind: ProxyProfile['kind'];
  host: string;
  port: string;
  maxConcurrentRuns: string;
  username: string;
  password: string;
};
