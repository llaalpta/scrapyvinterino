import { Info, Pause, Play, Power, Save } from 'lucide-react';
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
        <h3>Ajustes</h3>
        <span>{scheduler?.effective_enabled ? 'Scheduler activo' : 'Scheduler parado'}</span>
      </div>
      {scheduler ? (
        <div className="settings-body">
          <section className="settings-section">
            <div className="settings-section-heading">
              <div>
                <h4>Estado del scheduler</h4>
                <p>Control operativo y capacidad efectiva para monitores periodicos.</p>
              </div>
              <button type="button" disabled={savingScheduler} onClick={onToggleScheduler}>
                <Power size={17} />
                {scheduler.enabled ? 'Deshabilitar scheduler' : 'Habilitar scheduler'}
              </button>
            </div>
            <div className="settings-summary-grid">
              <SummaryItem label="Scheduler" value={scheduler.enabled ? 'Habilitado en la UI' : 'Deshabilitado en la UI'} />
              <SummaryItem label="Runtime" value={scheduler.runtime_enabled ? 'Permitido por .env' : 'Bloqueado por .env'} />
              <SummaryItem label="Capacidad" value={`${scheduler.active_periodic_monitors}/${scheduler.effective_capacity} monitores activos`} />
              <SummaryItem label="Egress" value={`${scheduler.proxy_capacity} proxy / ${scheduler.direct_capacity} directo`} />
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-heading compact">
              <div>
                <h4>Capacidad y salida</h4>
                <p>{getCapacityHint(scheduler)}</p>
              </div>
            </div>
            <div className="settings-grid">
              <NumberSetting
                id="max-concurrent-runs"
                label="Concurrencia global"
                help="Maximo de runs de monitores que pueden ejecutarse a la vez."
                min="1"
                max="20"
                value={scheduler.max_concurrent_runs}
                onBlur={(value) => updateNumber('max_concurrent_runs', value)}
              />
              <NumberSetting
                id="max-runs-per-proxy"
                label="Max por proxy"
                help="Maximo de runs simultaneos que puede usar cada perfil de proxy."
                min="1"
                max="10"
                value={scheduler.max_runs_per_proxy}
                onBlur={(value) => updateNumber('max_runs_per_proxy', value)}
              />
              <div className="settings-field settings-checkbox-field">
                <FieldHeading help="Permite ejecutar runs sin proxy cuando no hay proxy disponible.">Permitir directo</FieldHeading>
                <label className="settings-switch">
                  <input
                    type="checkbox"
                    checked={scheduler.allow_direct_without_proxy}
                    onChange={(event) => onUpdateSchedulerConfig({ allow_direct_without_proxy: event.target.checked })}
                  />
                  <span>Salida directa sin proxy</span>
                </label>
              </div>
              <NumberSetting
                id="direct-max-concurrent-runs"
                label="Directos max"
                help="Limite de runs simultaneos que pueden salir sin proxy."
                min="0"
                max="10"
                value={scheduler.direct_max_concurrent_runs}
                disabled={!scheduler.allow_direct_without_proxy}
                onBlur={(value) => updateNumber('direct_max_concurrent_runs', value)}
              />
              <NumberSetting
                id="proxy-cooldown-minutes"
                label="Cooldown proxy min"
                help="Minutos que un proxy fallido queda fuera de rotacion."
                min="1"
                max="1440"
                value={scheduler.proxy_cooldown_minutes}
                onBlur={(value) => updateNumber('proxy_cooldown_minutes', value)}
              />
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-heading compact">
              <div>
                <h4>Limites de ejecucion</h4>
                <p>Acota cuanto lee cada run y como reacciona ante fallos temporales.</p>
              </div>
            </div>
            <div className="settings-grid">
              <NumberSetting
                id="catalog-per-page"
                label="Resultados catalogo"
                help="Candidatos maximos leidos desde el catalogo de Vinted por run."
                min="1"
                max="96"
                value={scheduler.catalog_per_page}
                onBlur={(value) => updateNumber('catalog_per_page', value)}
              />
              <NumberSetting
                id="detail-max-candidates"
                label="Detalles por run"
                help="Candidatos maximos con detalle ampliado para aplicar filtros locales."
                min="0"
                max="96"
                value={scheduler.detail_max_candidates_per_run}
                onBlur={(value) => updateNumber('detail_max_candidates_per_run', value)}
              />
              <NumberSetting
                id="request-timeout-ms"
                label="Timeout ms"
                help="Tiempo maximo por peticion HTTP antes de considerarla fallida."
                min="1000"
                max="60000"
                step="1000"
                value={scheduler.request_timeout_ms}
                onBlur={(value) => updateNumber('request_timeout_ms', value)}
              />
              <NumberSetting
                id="request-retries"
                label="Reintentos"
                help="Intentos extra tras un fallo temporal de peticion."
                min="0"
                max="5"
                value={scheduler.request_retries}
                onBlur={(value) => updateNumber('request_retries', value)}
              />
              <NumberSetting
                id="stop-after-failures"
                label="Fallos antes de parar"
                help="Fallos consecutivos permitidos antes de detener el monitor."
                min="1"
                max="20"
                value={scheduler.stop_monitor_after_consecutive_failures}
                onBlur={(value) => updateNumber('stop_monitor_after_consecutive_failures', value)}
              />
            </div>
          </section>
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
          <div className="proxy-form-group">
            <div className="proxy-form-group-heading">
              <strong>Identidad</strong>
              <HelpTooltip text="Nombre interno y clase del proxy dentro del pool global." />
            </div>
            <label>
              Nombre
              <input value={proxyDraft.name} onChange={(event) => setProxyDraft({ ...proxyDraft, name: event.target.value })} required />
            </label>
            <label>
              Tipo
              <select value={proxyDraft.kind} onChange={(event) => setProxyDraft({ ...proxyDraft, kind: event.target.value as ProxyDraft['kind'] })}>
                <option value="own">Own</option>
                <option value="datacenter">Datacenter</option>
                <option value="residential">Residential</option>
              </select>
            </label>
            <label>
              Protocolo
              <select value={proxyDraft.scheme} onChange={(event) => setProxyDraft({ ...proxyDraft, scheme: event.target.value })}>
                <option value="http">http</option>
                <option value="https">https</option>
                <option value="socks5">socks5</option>
              </select>
            </label>
          </div>

          <div className="proxy-form-group wide">
            <div className="proxy-form-group-heading">
              <strong>Conexion</strong>
              <HelpTooltip text="Endpoint y credenciales del proxy. La password no se devuelve nunca desde la API." />
            </div>
            <label>
              Host
              <input value={proxyDraft.host} onChange={(event) => setProxyDraft({ ...proxyDraft, host: event.target.value })} required />
            </label>
            <label>
              Puerto
              <input
                value={proxyDraft.port}
                type="number"
                min="1"
                max="65535"
                onChange={(event) => setProxyDraft({ ...proxyDraft, port: event.target.value })}
                required
              />
            </label>
            <label>
              Usuario
              <input value={proxyDraft.username} onChange={(event) => setProxyDraft({ ...proxyDraft, username: event.target.value })} />
            </label>
            <label>
              Password
              <input value={proxyDraft.password} type="password" onChange={(event) => setProxyDraft({ ...proxyDraft, password: event.target.value })} />
            </label>
          </div>

          <div className="proxy-form-group proxy-form-actions">
            <div className="proxy-form-group-heading">
              <strong>Capacidad</strong>
              <HelpTooltip text="Runs simultaneos maximos para este proxy, siempre acotados por la concurrencia global." />
            </div>
            <label>
              Max runs proxy
              <input
                value={proxyDraft.maxConcurrentRuns}
                type="number"
                min="1"
                max="10"
                onChange={(event) => setProxyDraft({ ...proxyDraft, maxConcurrentRuns: event.target.value })}
                required
              />
            </label>
            <button type="submit" disabled={savingProxy}>
              <Save size={16} />
              Guardar proxy
            </button>
          </div>
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
                    {proxy.kind} | {proxy.scheme}://{proxy.username_masked ? `${proxy.username_masked}@` : ''}
                    {proxy.host}:{proxy.port} | max {proxy.max_concurrent_runs}
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

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="settings-summary-item">
      <strong>{label}</strong>
      <p>{value}</p>
    </div>
  );
}

function NumberSetting({
  disabled = false,
  help,
  id,
  label,
  max,
  min,
  onBlur,
  step,
  value
}: {
  disabled?: boolean;
  help: string;
  id: string;
  label: string;
  max: string;
  min: string;
  onBlur: (value: string) => void;
  step?: string;
  value: number;
}) {
  return (
    <div className="settings-field">
      <FieldHeading htmlFor={id} help={help}>
        {label}
      </FieldHeading>
      <input
        key={`${id}-${value}`}
        id={id}
        type="number"
        min={min}
        max={max}
        step={step}
        defaultValue={value}
        disabled={disabled}
        onBlur={(event) => onBlur(event.target.value)}
      />
    </div>
  );
}

function FieldHeading({
  children,
  help,
  htmlFor
}: {
  children: string;
  help: string;
  htmlFor?: string;
}) {
  return (
    <div className="field-heading">
      {htmlFor ? <label htmlFor={htmlFor}>{children}</label> : <span>{children}</span>}
      <HelpTooltip text={help} />
    </div>
  );
}

function HelpTooltip({ text }: { text: string }) {
  return (
    <button className="info-tooltip" type="button" aria-label={`Info: ${text}`}>
      <Info size={14} aria-hidden="true" />
      <span className="tooltip-bubble" role="tooltip">
        {text}
      </span>
    </button>
  );
}

function getCapacityHint(scheduler: SchedulerState) {
  if (scheduler.proxy_capacity > 0) {
    return 'Los proxys activos tienen prioridad antes de usar salida directa.';
  }
  if (scheduler.allow_direct_without_proxy && scheduler.direct_capacity > 0) {
    return 'Sin proxys activos: el scheduler puede usar salida directa limitada.';
  }
  return 'Sin capacidad de salida: activa proxys o permite salida directa.';
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
