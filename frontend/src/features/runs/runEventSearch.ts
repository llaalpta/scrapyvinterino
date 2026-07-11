import type { RunEvent } from '../../api';

export function eventSearchText(event: RunEvent): string {
  return [
    event.phase,
    event.phase.replaceAll('_', ' '),
    event.level,
    event.message,
    event.url,
    event.method,
    event.status_code,
    typeof event.duration_ms === 'number' ? `${event.duration_ms}ms` : null,
    event.auth_mode,
    event.proxy_profile_id,
    event.run_id ? `run#${event.run_id}` : null,
    JSON.stringify(event.details)
  ].filter(Boolean).join(' ').toLowerCase();
}
