import { Info, Pause, Play, Save } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import type { ProxyProfile, SchedulerState, SchedulerUpdate } from '../../api';
import type { CollectionLoadState } from '../../app/collectionLoadState';
import { formatDate } from '../../utils/format';
import { formatProxyCooldownRemaining, proxyCooldownRemainingMs } from '../../utils/proxyCooldown';

export function SettingsView({
  onCreateProxy,
  onToggleProxy,
  onUpdateProxyStickyContract,
  onUpdateSchedulerConfig,
  proxyDraft,
  proxyCollectionState,
  proxyCooldownNowMs,
  proxyProfiles,
  savingProxy,
  scheduler,
  schedulerAvailabilityError,
  setProxyDraft
}: {
  onCreateProxy: (event: FormEvent<HTMLFormElement>) => void;
  onToggleProxy: (profile: ProxyProfile) => void;
  onUpdateProxyStickyContract: (
    profile: ProxyProfile,
    stickyUsernameTemplate: string,
    stickyTtlMinutes: number
  ) => void;
  onUpdateSchedulerConfig: (payload: SchedulerUpdate) => void;
  proxyDraft: ProxyDraft;
  proxyCollectionState: CollectionLoadState;
  proxyCooldownNowMs: number;
  proxyProfiles: ProxyProfile[];
  savingProxy: boolean;
  scheduler: SchedulerState | null;
  schedulerAvailabilityError: string | null;
  setProxyDraft: (draft: ProxyDraft) => void;
}) {
  const updateNumber = (field: keyof SchedulerUpdate, value: string) => {
    if (!value) {
      return;
    }
    onUpdateSchedulerConfig({ [field]: Number(value) });
  };
  const schedulerStatus = scheduler ? getSchedulerStatus(scheduler) : null;

  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Ajustes</h3>
        <span>{schedulerStatus?.label ?? 'Scheduler no disponible'}</span>
      </div>
      {scheduler ? (
        <div className="settings-body">
          <section className="settings-section">
            <div className="settings-section-heading">
              <div>
                <h4>Estado del scheduler</h4>
                <p>{schedulerStatus?.description}</p>
              </div>
            </div>
            <div className="settings-summary-grid">
              <SummaryItem label="Runtime" value={scheduler.runtime_enabled ? 'Permitido por .env' : 'Bloqueado por .env'} />
              <SummaryItem label="Worker" value={getWorkerStatus(scheduler)} />
              <SummaryItem label="Capacidad" value={`${scheduler.active_periodic_monitors}/${scheduler.effective_capacity} monitores activos`} />
              <SummaryItem label="Egress" value={`${scheduler.proxy_capacity} proxy`} />
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
                label="Limite local simultaneo"
                help="Limite local de runs de monitores que pueden ejecutarse a la vez."
                min="1"
                max="20"
                value={scheduler.max_concurrent_runs}
                onBlur={(value) => updateNumber('max_concurrent_runs', value)}
              />
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-heading compact">
              <div>
                <h4>Limites por run</h4>
                <p>Acota cuanto lee cada ejecucion de monitor.</p>
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
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-heading compact">
              <div>
                <h4>Avanzado operativo</h4>
                <p>Ajustes de resiliencia para errores temporales y proxys fallidos.</p>
              </div>
            </div>
            <div className="settings-grid">
              <NumberSetting
                id="request-timeout-ms"
                label="Timeout HTTP"
                help="Tiempo maximo por peticion HTTP antes de considerarla fallida."
                min="1000"
                max="60000"
                step="1000"
                value={scheduler.request_timeout_ms}
                onBlur={(value) => updateNumber('request_timeout_ms', value)}
              />
              <NumberSetting
                id="proxy-cooldown-minutes"
                label="Pausa fallo proxy"
                help="Minutos que un proxy fallido queda fuera de rotacion."
                min="1"
                max="1440"
                value={scheduler.proxy_cooldown_minutes}
                onBlur={(value) => updateNumber('proxy_cooldown_minutes', value)}
              />
              <NumberSetting
                id="stop-after-failures"
                label="Parar tras fallos"
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
        <p className="empty-inline">{schedulerAvailabilityError ?? 'No se pudo cargar la configuracion del scheduler.'}</p>
      )}

      <div className="proxy-section">
        <div className="panel-heading nested">
          <h3>Proxy pool</h3>
          <span>
            {proxyCollectionState === 'loading'
              ? 'Cargando'
              : proxyCollectionState === 'unavailable'
                ? 'No disponible'
                : proxyProfiles.length}
          </span>
        </div>
        <form className="proxy-form" onSubmit={onCreateProxy}>
          <div className="proxy-form-intro">
            <div>
              <strong>Nuevo proxy</strong>
              <p>Pool global obligatorio para ejecutar catalogos de Vinted.</p>
            </div>
            <HelpTooltip text="Credenciales cifradas en reposo. La API no devuelve passwords ni se asignan proxys manualmente por monitor." />
          </div>
          <div className="proxy-form-fields">
            <label className="wide-field">
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
            <label className="wide-field">
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
              Limite local
              <input
                value={proxyDraft.maxConcurrentRuns}
                type="number"
                min="1"
                max="10"
                onChange={(event) => setProxyDraft({ ...proxyDraft, maxConcurrentRuns: event.target.value })}
                required
              />
            </label>
            <label className="wide-field">
              Plantilla sticky
              <input
                value={proxyDraft.stickyUsernameTemplate}
                maxLength={255}
                onChange={(event) => setProxyDraft({ ...proxyDraft, stickyUsernameTemplate: event.target.value })}
                required
              />
            </label>
            <label>
              TTL sticky (min)
              <input
                value={proxyDraft.stickyTtlMinutes}
                type="number"
                min="1"
                max="120"
                onChange={(event) => setProxyDraft({ ...proxyDraft, stickyTtlMinutes: event.target.value })}
                required
              />
            </label>
            <label>
              Pais
              <input
                value={proxyDraft.countryCode}
                maxLength={2}
                onChange={(event) => setProxyDraft({ ...proxyDraft, countryCode: event.target.value.toUpperCase() })}
                required
              />
            </label>
            <label>
              Usuario
              <input value={proxyDraft.username} onChange={(event) => setProxyDraft({ ...proxyDraft, username: event.target.value })} required />
            </label>
            <label>
              Password
              <input value={proxyDraft.password} type="password" onChange={(event) => setProxyDraft({ ...proxyDraft, password: event.target.value })} required />
            </label>
          </div>
          <div className="proxy-form-actions">
            <button type="submit" disabled={savingProxy || proxyCollectionState !== 'ready'}>
              <Save size={16} />
              Guardar proxy
            </button>
          </div>
        </form>
        {proxyCollectionState !== 'ready' ? (
          <p className="empty-inline" role="status">
            {proxyCollectionState === 'loading'
              ? 'Cargando proxys...'
              : 'Proxys no disponibles. Recarga la PWA para reintentar.'}
          </p>
        ) : proxyProfiles.length === 0 ? (
          <p className="empty-inline">Sin proxys configurados. Los runs de catalogo quedan bloqueados.</p>
        ) : (
          <div className="proxy-list">
            {proxyProfiles.map((proxy) => {
              const cooldownUntil = proxy.cooldown_until;
              const cooldownRemainingMs = proxyCooldownRemainingMs(proxy, proxyCooldownNowMs);
              return (
                <article className="proxy-row" key={proxy.id}>
                  <div>
                    <strong>{proxy.name}</strong>
                    <span>
                      {proxy.kind} | {proxy.scheme}://{proxy.username_masked ? `${proxy.username_masked}@` : ''}
                      {proxy.host}:{proxy.port} | limite local {proxy.max_concurrent_runs}
                    </span>
                    <span>
                      Contexto resuelto: {proxy.country_code} | {proxy.locale} | viewport {proxy.screen} | x-screen {proxy.vinted_screen}
                    </span>
                    <ProxyStickyContractEditor
                      disabled={savingProxy}
                      key={`${proxy.id}:${proxy.sticky_username_template}:${proxy.sticky_ttl_minutes}`}
                      onSave={onUpdateProxyStickyContract}
                      proxy={proxy}
                    />
                    {cooldownRemainingMs !== null && cooldownUntil ? (
                      <span className="proxy-action-message failed">
                        {proxy.failure_count} fallos | hasta {formatDate(cooldownUntil)} | restan {formatProxyCooldownRemaining(cooldownRemainingMs)}
                      </span>
                    ) : null}
                  </div>
                  <span className={proxy.is_active ? 'status active' : 'status'}>{proxy.is_active ? 'Activo' : 'Pausado'}</span>
                  {cooldownRemainingMs !== null ? <span className="status failed">Cooldown activo</span> : null}
                  <button type="button" onClick={() => onToggleProxy(proxy)}>
                    {proxy.is_active ? <Pause size={16} /> : <Play size={16} />}
                    {proxy.is_active ? 'Pausar' : 'Activar'}
                  </button>
                </article>
              );
            })}
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
  if (!scheduler.worker_available) {
    return 'El worker no esta disponible: recuperalo antes de lanzar monitores periodicos.';
  }
  if (scheduler.proxy_capacity <= 0) {
    return 'Configura un proxy activo de ES antes de lanzar monitores.';
  }
  return 'La capacidad de salida procede exclusivamente de los proxys activos.';
}

function getSchedulerStatus(scheduler: SchedulerState) {
  if (scheduler.effective_enabled) {
    return {
      label: 'Scheduler activo',
      description: 'El productor esta disponible para ejecutar monitores periodicos.'
    };
  }
  if (scheduler.runtime_enabled && !scheduler.worker_available) {
    return {
      label: 'Scheduler no disponible',
      description: 'El productor no esta disponible: no se pueden iniciar ni ejecutar monitores periodicos.'
    };
  }
  if (!scheduler.runtime_enabled) {
    return {
      label: 'Scheduler bloqueado',
      description: 'El despliegue bloquea las sesiones periodicas mediante .env.'
    };
  }
  if (scheduler.runtime_enabled) {
    return {
      label: 'Scheduler sin capacidad',
      description: 'El productor esta disponible, pero falta capacidad de salida para monitores periodicos.'
    };
  }
  return { label: 'Scheduler no disponible', description: 'No se pudo determinar su disponibilidad.' };
}

function getWorkerStatus(scheduler: SchedulerState) {
  const lastSeen = formatWorkerLastSeen(scheduler.worker_last_seen_at);
  if (!scheduler.worker_available) {
    return lastSeen ? `No disponible; ultima senal ${lastSeen}` : 'No disponible; sin senal registrada';
  }
  return lastSeen ? `Disponible; ultima senal ${lastSeen}` : 'Disponible';
}

function formatWorkerLastSeen(value: string | null) {
  if (!value) {
    return null;
  }
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return null;
  }
  return new Intl.DateTimeFormat('es-ES', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
    second: '2-digit'
  }).format(new Date(timestamp));
}

export type ProxyDraft = {
  name: string;
  scheme: string;
  kind: ProxyProfile['kind'];
  host: string;
  port: string;
  maxConcurrentRuns: string;
  stickyUsernameTemplate: string;
  stickyTtlMinutes: string;
  username: string;
  password: string;
  countryCode: string;
};

function ProxyStickyContractEditor({
  disabled,
  onSave,
  proxy
}: {
  disabled: boolean;
  onSave: (
    profile: ProxyProfile,
    stickyUsernameTemplate: string,
    stickyTtlMinutes: number
  ) => void;
  proxy: ProxyProfile;
}) {
  const [template, setTemplate] = useState(proxy.sticky_username_template);
  const [ttlMinutes, setTtlMinutes] = useState(String(proxy.sticky_ttl_minutes));

  const parsedTtlMinutes = Number(ttlMinutes);
  const changed = template !== proxy.sticky_username_template
    || parsedTtlMinutes !== proxy.sticky_ttl_minutes;

  return (
    <form
      className="proxy-sticky-editor"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(proxy, template, parsedTtlMinutes);
      }}
    >
      <label>
        Plantilla sticky
        <input
          aria-label={`Plantilla sticky de ${proxy.name}`}
          value={template}
          maxLength={255}
          onChange={(event) => setTemplate(event.target.value)}
          required
        />
      </label>
      <label>
        TTL (min)
        <input
          aria-label={`TTL sticky de ${proxy.name}`}
          value={ttlMinutes}
          type="number"
          min="1"
          max="120"
          onChange={(event) => setTtlMinutes(event.target.value)}
          required
        />
      </label>
      <button type="submit" disabled={disabled || !changed}>
        <Save size={15} />
        Guardar sticky
      </button>
    </form>
  );
}
