import type { ProxyProfile } from '../api';

export function proxyCooldownRemainingMs(profile: ProxyProfile, nowMs: number): number | null {
  if (!profile.cooldown_until) {
    return null;
  }
  const expiresAtMs = Date.parse(profile.cooldown_until);
  if (!Number.isFinite(expiresAtMs) || expiresAtMs <= nowMs) {
    return null;
  }
  return expiresAtMs - nowMs;
}

export function formatProxyCooldownRemaining(remainingMs: number): string {
  const remainingSeconds = Math.max(1, Math.ceil(remainingMs / 1000));
  const hours = Math.floor(remainingSeconds / 3600);
  const minutes = Math.floor((remainingSeconds % 3600) / 60);
  const seconds = remainingSeconds % 60;
  if (hours > 0) {
    return `${hours} h ${minutes} min`;
  }
  if (minutes > 0) {
    return `${minutes} min ${seconds} s`;
  }
  return `${seconds} s`;
}

export function allActiveProxiesCooling(profiles: ProxyProfile[], nowMs: number): boolean {
  const activeProfiles = profiles.filter((profile) => profile.is_active);
  return activeProfiles.length > 0
    && activeProfiles.every((profile) => proxyCooldownRemainingMs(profile, nowMs) !== null);
}
