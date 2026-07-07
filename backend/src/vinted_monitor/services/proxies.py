from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.crypto import decrypt_text, encrypt_text, fingerprint_text, mask_text
from vinted_monitor.core.redaction import redact_sensitive_text
from vinted_monitor.db.models import ProxyProfile

PROXY_KINDS = {"own", "datacenter", "residential"}
DEFAULT_PROXY_COUNTRY_CODE = "ES"
DEFAULT_PROXY_LOCALE = "es-ES"
DEFAULT_PROXY_ACCEPT_LANGUAGE = "es-ES,es;q=0.9,en;q=0.8"
DEFAULT_PROXY_SCREEN = "1920x1080"
SCREEN_PATTERN = re.compile(r"^\d{3,5}x\d{3,5}$")


class ProxyProfileNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class ProxyPublicFields:
    id: int
    name: str
    scheme: str
    kind: str
    host: str
    port: int
    username: str | None
    username_masked: str | None
    has_password: bool
    password_fingerprint: str | None
    country_code: str
    locale: str
    accept_language: str
    screen: str
    is_active: bool
    max_concurrent_runs: int
    cooldown_until: datetime | None
    failure_count: int
    last_used_at: datetime | None
    last_test_status: str | None
    last_test_ip: str | None
    last_test_error: str | None


def list_proxy_profiles(db: Session) -> list[ProxyProfile]:
    return list(db.scalars(select(ProxyProfile).order_by(ProxyProfile.id.desc())))


def list_available_proxy_profiles(db: Session, *, now: datetime | None = None, country_code: str | None = None) -> list[ProxyProfile]:
    current_time = now or datetime.now(UTC)
    statement = (
        select(ProxyProfile)
        .where(
            ProxyProfile.is_active.is_(True),
            (ProxyProfile.cooldown_until.is_(None) | (ProxyProfile.cooldown_until <= current_time)),
        )
        .order_by(ProxyProfile.failure_count.asc(), ProxyProfile.last_used_at.asc().nullsfirst(), ProxyProfile.id.asc())
    )
    if country_code:
        statement = statement.where(ProxyProfile.country_code == country_code.strip().upper())
    return list(
        db.scalars(statement)
    )


def create_proxy_profile(
    db: Session,
    *,
    name: str,
    scheme: str,
    kind: str = "own",
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    country_code: str = DEFAULT_PROXY_COUNTRY_CODE,
    locale: str = DEFAULT_PROXY_LOCALE,
    accept_language: str = DEFAULT_PROXY_ACCEPT_LANGUAGE,
    screen: str = DEFAULT_PROXY_SCREEN,
    max_concurrent_runs: int = 1,
    is_active: bool = True,
    settings: Settings | None = None,
) -> ProxyProfile:
    settings = settings or get_settings()
    profile = ProxyProfile(
        name=_validate_name(name),
        scheme=_validate_scheme(scheme),
        kind=_validate_kind(kind),
        host=_validate_host(host),
        port=_validate_port(port),
        username=username.strip() if username else None,
        password_encrypted=_encrypt_password(password, settings) if password else None,
        country_code=_validate_country_code(country_code),
        locale=_validate_locale(locale, country_code),
        accept_language=_validate_accept_language(accept_language, locale),
        screen=_validate_screen(screen),
        max_concurrent_runs=_validate_max_concurrent_runs(max_concurrent_runs),
        is_active=is_active,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def update_proxy_profile(
    db: Session,
    profile_id: int,
    *,
    name: str | None = None,
    scheme: str | None = None,
    kind: str | None = None,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    clear_password: bool = False,
    country_code: str | None = None,
    locale: str | None = None,
    accept_language: str | None = None,
    screen: str | None = None,
    max_concurrent_runs: int | None = None,
    is_active: bool | None = None,
    settings: Settings | None = None,
) -> ProxyProfile:
    settings = settings or get_settings()
    profile = db.get(ProxyProfile, profile_id)
    if profile is None:
        raise ProxyProfileNotFoundError(f"Proxy profile {profile_id} does not exist")
    if name is not None:
        profile.name = _validate_name(name)
    if scheme is not None:
        profile.scheme = _validate_scheme(scheme)
    if kind is not None:
        profile.kind = _validate_kind(kind)
    if host is not None:
        profile.host = _validate_host(host)
    if port is not None:
        profile.port = _validate_port(port)
    if username is not None:
        profile.username = username.strip() or None
    if clear_password:
        profile.password_encrypted = None
    elif password:
        profile.password_encrypted = _encrypt_password(password, settings)
    next_country_code = _validate_country_code(country_code) if country_code is not None else profile.country_code
    next_locale = _validate_locale(locale if locale is not None else profile.locale, next_country_code)
    next_accept_language = _validate_accept_language(
        accept_language if accept_language is not None else profile.accept_language,
        next_locale,
    )
    if country_code is not None:
        profile.country_code = next_country_code
    if locale is not None or country_code is not None:
        profile.locale = next_locale
    if accept_language is not None or locale is not None or country_code is not None:
        profile.accept_language = next_accept_language
    if screen is not None:
        profile.screen = _validate_screen(screen)
    if max_concurrent_runs is not None:
        profile.max_concurrent_runs = _validate_max_concurrent_runs(max_concurrent_runs)
    if is_active is not None:
        profile.is_active = is_active
    db.commit()
    db.refresh(profile)
    return profile


def mark_proxy_used(db: Session, profile_id: int) -> None:
    profile = db.get(ProxyProfile, profile_id)
    if profile is None:
        return
    profile.last_used_at = datetime.now(UTC)
    db.flush()


def mark_proxy_run_success(db: Session, profile_id: int | None) -> None:
    if profile_id is None:
        return
    profile = db.get(ProxyProfile, profile_id)
    if profile is None:
        return
    profile.failure_count = 0
    profile.cooldown_until = None


def mark_proxy_run_failure(db: Session, profile_id: int | None, *, cooldown_minutes: int = 10) -> None:
    if profile_id is None:
        return
    profile = db.get(ProxyProfile, profile_id)
    if profile is None:
        return
    profile.failure_count = (profile.failure_count or 0) + 1
    # Exponential backoff capped at 24 hours
    backoff = min(cooldown_minutes * (2 ** (profile.failure_count - 1)), 1440)
    profile.cooldown_until = datetime.now(UTC) + timedelta(minutes=max(backoff, 1))


def mark_proxy_challenge_detected(db: Session, profile_id: int | None, *, penalty_multiplier: int = 2, cooldown_minutes: int = 10) -> None:
    """DataDome challenge: apply a multiplied penalty before exponential cooldown."""
    if profile_id is None:
        return
    profile = db.get(ProxyProfile, profile_id)
    if profile is None:
        return
    profile.failure_count = (profile.failure_count or 0) + penalty_multiplier
    backoff = min(cooldown_minutes * (2 ** (profile.failure_count - 1)), 1440)
    profile.cooldown_until = datetime.now(UTC) + timedelta(minutes=max(backoff, 1))


def mark_proxy_test_result(db: Session, profile_id: int, *, status: str, ip: str | None = None, error: str | None = None) -> ProxyProfile:
    profile = db.get(ProxyProfile, profile_id)
    if profile is None:
        raise ProxyProfileNotFoundError(f"Proxy profile {profile_id} does not exist")
    profile.last_test_status = status
    profile.last_test_ip = ip
    profile.last_test_error = redact_sensitive_text(error) if error else None
    db.commit()
    db.refresh(profile)
    return profile


def profile_to_public_fields(profile: ProxyProfile, settings: Settings | None = None) -> ProxyPublicFields:
    settings = settings or get_settings()
    password = _decrypt_password(profile, settings) if profile.password_encrypted else None
    return ProxyPublicFields(
        id=profile.id,
        name=profile.name,
        scheme=profile.scheme,
        kind=profile.kind,
        host=profile.host,
        port=profile.port,
        username=profile.username,
        username_masked=mask_text(profile.username),
        has_password=bool(profile.password_encrypted),
        password_fingerprint=fingerprint_text(password) if password else None,
        country_code=profile.country_code,
        locale=profile.locale,
        accept_language=profile.accept_language,
        screen=profile.screen,
        is_active=profile.is_active,
        max_concurrent_runs=profile.max_concurrent_runs,
        cooldown_until=profile.cooldown_until,
        failure_count=profile.failure_count,
        last_used_at=profile.last_used_at,
        last_test_status=profile.last_test_status,
        last_test_ip=profile.last_test_ip,
        last_test_error=profile.last_test_error,
    )


def proxy_url_for_profile(profile: ProxyProfile | None, settings: Settings | None = None) -> str | None:
    if profile is None:
        return None
    settings = settings or get_settings()
    auth = ""
    if profile.username:
        password = _decrypt_password(profile, settings) if profile.password_encrypted else ""
        auth = f"{quote(profile.username)}:{quote(password)}@"
    return f"{profile.scheme}://{auth}{profile.host}:{profile.port}"


def proxy_url_with_sticky_session(
    profile: ProxyProfile | None,
    session_id: str,
    settings: Settings | None = None,
    username_template: str | None = None,
) -> str | None:
    """Build a proxy URL with a dynamic sticky session UUID.

    Injects the session_id into the username for residential proxy gateways
    that support session persistence. The default template is
    ``{username}-session-{session_id}``; providers such as Oxylabs can use
    ``{username}-sessid-{session_id}``.
    """
    if profile is None:
        return None
    settings = settings or get_settings()
    if not profile.username:
        return proxy_url_for_profile(profile, settings)
    password = _decrypt_password(profile, settings) if profile.password_encrypted else ""
    template = username_template or settings.proxy_sticky_username_template
    try:
        sticky_username = template.format(username=profile.username, session_id=session_id)
    except KeyError as exc:
        raise ValueError("Proxy sticky username template only supports {username} and {session_id}") from exc
    if profile.username not in sticky_username or session_id not in sticky_username:
        raise ValueError("Proxy sticky username template must include {username} and {session_id}")
    auth = f"{quote(sticky_username)}:{quote(password)}@"
    return f"{profile.scheme}://{auth}{profile.host}:{profile.port}"


def _encrypt_password(password: str, settings: Settings) -> str:
    return encrypt_text(password, settings.app_secret_key)


def _decrypt_password(profile: ProxyProfile, settings: Settings) -> str:
    if not profile.password_encrypted:
        return ""
    return decrypt_text(profile.password_encrypted, settings.app_secret_key)


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Proxy profile name cannot be empty")
    return cleaned


def _validate_scheme(scheme: str) -> str:
    cleaned = scheme.strip().lower()
    if cleaned not in {"http", "https", "socks5"}:
        raise ValueError("Proxy scheme must be http, https, or socks5")
    return cleaned


def _validate_kind(kind: str) -> str:
    cleaned = kind.strip().lower()
    if cleaned not in PROXY_KINDS:
        raise ValueError("Proxy kind must be own, datacenter, or residential")
    return cleaned


def _validate_country_code(value: str) -> str:
    cleaned = value.strip().upper()
    if len(cleaned) != 2 or not cleaned.isalpha():
        raise ValueError("Proxy country_code must be an ISO 3166-1 alpha-2 code")
    return cleaned


def _validate_locale(value: str, country_code: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Proxy locale cannot be empty")
    if "-" not in cleaned:
        raise ValueError("Proxy locale must include language and country, for example es-ES")
    locale_country = cleaned.rsplit("-", 1)[-1].upper()
    if locale_country != country_code.strip().upper():
        raise ValueError("Proxy locale country must match country_code")
    return cleaned


def _validate_accept_language(value: str, locale: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Proxy accept_language cannot be empty")
    if not cleaned.lower().startswith(locale.lower()):
        raise ValueError("Proxy accept_language must start with locale")
    return cleaned


def _validate_screen(value: str) -> str:
    cleaned = value.strip().lower()
    if not SCREEN_PATTERN.match(cleaned):
        raise ValueError("Proxy screen must use WIDTHxHEIGHT format")
    return cleaned


def _validate_host(host: str) -> str:
    cleaned = host.strip()
    if not cleaned:
        raise ValueError("Proxy host cannot be empty")
    return cleaned


def _validate_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise ValueError("Proxy port must be between 1 and 65535")
    return port


def _validate_max_concurrent_runs(value: int) -> int:
    if value < 1 or value > 10:
        raise ValueError("Proxy max_concurrent_runs must be between 1 and 10")
    return value
