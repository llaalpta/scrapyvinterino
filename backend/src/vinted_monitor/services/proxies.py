from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from string import Formatter
from urllib.parse import quote

from cryptography.fernet import InvalidToken
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.crypto import decrypt_text, encrypt_text, fingerprint_text, mask_text
from vinted_monitor.db.models import ProxyProfile

PROXY_KINDS = {"own", "datacenter", "residential"}
DEFAULT_PROXY_COUNTRY_CODE = "ES"
PROXY_IDENTITY_FINGERPRINT_VERSION = "v1"
PROXY_IDENTITY_LOCK_NAMESPACE = 814_208_010
SCREEN_PATTERN = re.compile(r"^\d{3,5}x\d{3,5}$")
DEFAULT_STICKY_USERNAME_TEMPLATE = "{username};sessid.{session_id}"
DEFAULT_STICKY_TTL_MINUTES = 25
MAX_STICKY_USERNAME_TEMPLATE_LENGTH = 255


class ProxyProfileNotFoundError(ValueError):
    pass


class ProxyProfileEligibilityError(ValueError):
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
    vinted_screen: str
    sticky_username_template: str
    sticky_ttl_minutes: int
    is_active: bool
    max_concurrent_runs: int
    cooldown_until: datetime | None
    failure_count: int
    last_used_at: datetime | None


@dataclass(frozen=True)
class ProxyContextPreset:
    country_code: str
    locale: str
    accept_language: str
    screen: str
    vinted_screen: str


def _context_preset(
    country_code: str,
    locale: str,
    accept_language: str,
    screen: str,
    vinted_screen: str = "catalog",
) -> ProxyContextPreset:
    return ProxyContextPreset(
        country_code=_validate_country_code(country_code),
        locale=_validate_locale(locale, country_code),
        accept_language=_validate_accept_language(accept_language, locale),
        screen=_validate_screen(screen),
        vinted_screen=_validate_vinted_screen(vinted_screen),
    )


def resolve_proxy_context(country_code: str = DEFAULT_PROXY_COUNTRY_CODE) -> ProxyContextPreset:
    code = _validate_country_code(country_code)
    preset = PROXY_CONTEXT_PRESETS.get(code)
    if preset is None:
        supported = ", ".join(sorted(PROXY_CONTEXT_PRESETS))
        raise ValueError(f"Unsupported proxy country_code {code}; add an internal egress context preset. Supported: {supported}")
    return preset


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
    sticky_username_template: str = DEFAULT_STICKY_USERNAME_TEMPLATE,
    sticky_ttl_minutes: int = DEFAULT_STICKY_TTL_MINUTES,
    max_concurrent_runs: int = 1,
    is_active: bool = True,
    settings: Settings | None = None,
) -> ProxyProfile:
    settings = settings or get_settings()
    context = resolve_proxy_context(country_code)
    profile = ProxyProfile(
        name=_validate_name(name),
        scheme=_validate_scheme(scheme),
        kind=_validate_kind(kind),
        host=_validate_host(host),
        port=_validate_port(port),
        username=username.strip() if username else None,
        password_encrypted=_encrypt_password(password, settings) if password else None,
        country_code=context.country_code,
        locale=context.locale,
        accept_language=context.accept_language,
        screen=context.screen,
        vinted_screen=context.vinted_screen,
        sticky_username_template=_validate_sticky_username_template(sticky_username_template),
        sticky_ttl_minutes=_validate_sticky_ttl_minutes(sticky_ttl_minutes),
        max_concurrent_runs=_validate_max_concurrent_runs(max_concurrent_runs),
        is_active=is_active,
        identity_generation=1,
    )
    _validate_complete_proxy_configuration(profile, settings)
    profile.identity_fingerprint = effective_proxy_identity_fingerprint(profile, settings)
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
    sticky_username_template: str | None = None,
    sticky_ttl_minutes: int | None = None,
    max_concurrent_runs: int | None = None,
    is_active: bool | None = None,
    settings: Settings | None = None,
) -> ProxyProfile:
    settings = settings or get_settings()
    validated_sticky_username_template = (
        _validate_sticky_username_template(sticky_username_template)
        if sticky_username_template is not None
        else None
    )
    validated_sticky_ttl_minutes = (
        _validate_sticky_ttl_minutes(sticky_ttl_minutes)
        if sticky_ttl_minutes is not None
        else None
    )
    _acquire_proxy_identity_lock(db, profile_id, exclusive=True)
    profile = _lock_proxy_profile(db, profile_id)
    if profile is None:
        raise ProxyProfileNotFoundError(f"Proxy profile {profile_id} does not exist")
    synchronize_proxy_identity(db, profile, settings)
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
    if country_code is not None:
        context = resolve_proxy_context(country_code)
        profile.country_code = context.country_code
        profile.locale = context.locale
        profile.accept_language = context.accept_language
        profile.screen = context.screen
        profile.vinted_screen = context.vinted_screen
    if validated_sticky_username_template is not None:
        profile.sticky_username_template = validated_sticky_username_template
    if validated_sticky_ttl_minutes is not None:
        profile.sticky_ttl_minutes = validated_sticky_ttl_minutes
    if max_concurrent_runs is not None:
        profile.max_concurrent_runs = _validate_max_concurrent_runs(max_concurrent_runs)
    if is_active is not None:
        profile.is_active = is_active
    if profile.is_active:
        _validate_complete_proxy_configuration(profile, settings)
    synchronize_proxy_identity(db, profile, settings)
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
        vinted_screen=profile.vinted_screen,
        sticky_username_template=profile.sticky_username_template,
        sticky_ttl_minutes=profile.sticky_ttl_minutes,
        is_active=profile.is_active,
        max_concurrent_runs=profile.max_concurrent_runs,
        cooldown_until=profile.cooldown_until,
        failure_count=profile.failure_count,
        last_used_at=profile.last_used_at,
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
) -> str | None:
    """Build a proxy URL with a dynamic sticky session UUID.

    Injects the session_id into the username for residential proxy gateways
    that support session persistence. The format is owned by the selected
    proxy profile rather than process configuration.
    """
    if profile is None:
        return None
    settings = settings or get_settings()
    if not profile.username:
        return proxy_url_for_profile(profile, settings)
    password = _decrypt_password(profile, settings) if profile.password_encrypted else ""
    template = _validate_sticky_username_template(profile.sticky_username_template)
    try:
        sticky_username = template.format(username=profile.username, session_id=session_id)
    except KeyError as exc:
        raise ValueError("Proxy sticky username template only supports {username} and {session_id}") from exc
    if profile.username not in sticky_username or session_id not in sticky_username:
        raise ValueError("Proxy sticky username template must include {username} and {session_id}")
    auth = f"{quote(sticky_username)}:{quote(password)}@"
    return f"{profile.scheme}://{auth}{profile.host}:{profile.port}"


def effective_proxy_identity_fingerprint(profile: ProxyProfile, settings: Settings | None = None) -> str:
    """Return a keyed, versioned digest of the effective proxy transport identity."""
    settings = settings or get_settings()
    sticky_username_template = _validate_sticky_username_template(profile.sticky_username_template)
    sticky_ttl_minutes = _validate_sticky_ttl_minutes(profile.sticky_ttl_minutes)
    canonical_identity = json.dumps(
        {
            "accept_language": profile.accept_language,
            "country_code": profile.country_code,
            "host": profile.host,
            "locale": profile.locale,
            "password": _decrypt_password(profile, settings),
            "port": profile.port,
            "scheme": profile.scheme,
            "screen": profile.screen,
            "sticky_username_template": sticky_username_template,
            "sticky_ttl_minutes": sticky_ttl_minutes,
            "username": profile.username or "",
            "vinted_screen": profile.vinted_screen,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signing_key = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        b"vinted-monitor:effective-proxy-identity:v1",
        sha256,
    ).digest()
    digest = hmac.new(signing_key, canonical_identity, sha256).hexdigest()
    return f"{PROXY_IDENTITY_FINGERPRINT_VERSION}:{digest}"


def effective_proxy_identity_generation(profile: ProxyProfile) -> str:
    fingerprint = str(profile.identity_fingerprint or "")
    prefix = f"{PROXY_IDENTITY_FINGERPRINT_VERSION}:"
    if not fingerprint.startswith(prefix) or len(fingerprint) != len(prefix) + 64:
        raise ProxyProfileEligibilityError(f"Proxy profile {profile.id} has no effective identity fingerprint")
    generation = int(profile.identity_generation or 0)
    if generation <= 0:
        raise ProxyProfileEligibilityError(f"Proxy profile {profile.id} has no effective identity generation")
    return f"{PROXY_IDENTITY_FINGERPRINT_VERSION}:{generation}:{fingerprint.removeprefix(prefix)}"


def synchronize_proxy_identity(db: Session, profile: ProxyProfile, settings: Settings | None = None) -> bool:
    """Persist a new generation and purge old session context after identity drift."""
    settings = settings or get_settings()
    current_fingerprint = effective_proxy_identity_fingerprint(profile, settings)
    stored_fingerprint = profile.identity_fingerprint
    if stored_fingerprint and hmac.compare_digest(stored_fingerprint, current_fingerprint):
        return False

    if stored_fingerprint:
        profile.identity_generation = max(int(profile.identity_generation or 0), 0) + 1
    else:
        profile.identity_generation = max(int(profile.identity_generation or 0), 1)
    profile.identity_fingerprint = current_fingerprint
    db.flush()

    from vinted_monitor.services.vinted_sessions import invalidate_vinted_sessions_for_proxy_identity

    invalidate_vinted_sessions_for_proxy_identity(
        db,
        profile.id,
        reason="Prepared Vinted session proxy identity changed",
        settings=settings,
    )
    db.flush()
    return True


def lock_proxy_profile_for_selection(
    db: Session,
    profile_id: int,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> ProxyProfile:
    """Lock, synchronize and revalidate one profile selected for new work."""
    settings = settings or get_settings()
    profile = _load_proxy_profile(db, profile_id)
    if profile is None:
        raise ProxyProfileEligibilityError(f"Proxy profile {profile_id} no longer exists")
    try:
        _validate_sticky_username_template(profile.sticky_username_template)
        _validate_sticky_ttl_minutes(profile.sticky_ttl_minutes)
    except ValueError as exc:
        raise ProxyProfileEligibilityError("Proxy sticky contract is invalid") from exc
    current_fingerprint = effective_proxy_identity_fingerprint(profile, settings)
    stored_fingerprint = profile.identity_fingerprint
    fingerprint_matches = bool(
        stored_fingerprint and hmac.compare_digest(stored_fingerprint, current_fingerprint)
    )
    _acquire_proxy_identity_lock(db, profile_id, exclusive=not fingerprint_matches)
    if fingerprint_matches:
        profile = _load_proxy_profile(db, profile_id)
        if profile is None:
            raise ProxyProfileEligibilityError(f"Proxy profile {profile_id} no longer exists")
        reloaded_fingerprint = effective_proxy_identity_fingerprint(profile, settings)
        if not profile.identity_fingerprint or not hmac.compare_digest(
            profile.identity_fingerprint,
            reloaded_fingerprint,
        ):
            raise ProxyProfileEligibilityError(
                f"Proxy profile {profile_id} identity changed while acquiring its execution fence"
            )
    else:
        profile = _lock_proxy_profile(db, profile_id)
        if profile is None:
            raise ProxyProfileEligibilityError(f"Proxy profile {profile_id} no longer exists")
        synchronize_proxy_identity(db, profile, settings)
    _validate_proxy_profile_eligibility(profile, settings, now=now)
    return profile


def lock_and_revalidate_proxy_selection(
    db: Session,
    profile_id: int,
    captured_identity_generation: str | None,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> ProxyProfile:
    """Fence captured work against the current locked profile immediately pre-provider."""
    profile = lock_proxy_profile_for_selection(db, profile_id, settings, now=now)
    current_generation = effective_proxy_identity_generation(profile)
    if not isinstance(captured_identity_generation, str) or not hmac.compare_digest(
        captured_identity_generation,
        current_generation,
    ):
        raise ProxyProfileEligibilityError(f"Proxy profile {profile_id} identity changed after egress selection")
    return profile


def _lock_proxy_profile(db: Session, profile_id: int) -> ProxyProfile | None:
    return db.scalar(
        select(ProxyProfile)
        .where(ProxyProfile.id == profile_id)
        # Identity writers never change the profile primary key. NO KEY UPDATE
        # serializes those writers without conflicting with FK key-share locks
        # held by auditable run events for an already admitted execution.
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )


def _load_proxy_profile(db: Session, profile_id: int) -> ProxyProfile | None:
    return db.scalar(
        select(ProxyProfile)
        .where(ProxyProfile.id == profile_id)
        .execution_options(populate_existing=True)
    )


def _acquire_proxy_identity_lock(db: Session, profile_id: int, *, exclusive: bool) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    lock_function = func.pg_advisory_xact_lock if exclusive else func.pg_advisory_xact_lock_shared
    db.execute(select(lock_function(PROXY_IDENTITY_LOCK_NAMESPACE, profile_id)))


def _validate_proxy_profile_eligibility(
    profile: ProxyProfile,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(UTC)
    _validate_complete_proxy_configuration(profile, settings)
    if not profile.is_active:
        raise ProxyProfileEligibilityError(f"Proxy profile {profile.id} is inactive")
    if profile.cooldown_until is not None and profile.cooldown_until > current_time:
        raise ProxyProfileEligibilityError(f"Proxy profile {profile.id} is cooling down")
    target_country_code = settings.vinted_target_country_code.strip().upper()
    if profile.country_code != target_country_code:
        raise ProxyProfileEligibilityError(
            f"Proxy profile {profile.id} country does not match target country {target_country_code}"
        )
    expected_context = resolve_proxy_context(profile.country_code)
    actual_context = (
        profile.country_code,
        profile.locale,
        profile.accept_language,
        profile.screen,
        profile.vinted_screen,
    )
    canonical_context = (
        expected_context.country_code,
        expected_context.locale,
        expected_context.accept_language,
        expected_context.screen,
        expected_context.vinted_screen,
    )
    if actual_context != canonical_context:
        raise ProxyProfileEligibilityError(f"Proxy profile {profile.id} has a non-canonical country context preset")


def _validate_complete_proxy_configuration(profile: ProxyProfile, settings: Settings) -> None:
    try:
        _validate_host(profile.host)
        _validate_port(profile.port)
    except ValueError as exc:
        raise ProxyProfileEligibilityError("Proxy transport configuration is invalid") from exc
    try:
        _validate_sticky_username_template(profile.sticky_username_template)
        _validate_sticky_ttl_minutes(profile.sticky_ttl_minutes)
    except ValueError as exc:
        raise ProxyProfileEligibilityError("Proxy sticky contract is invalid") from exc
    if not profile.username or not profile.username.strip():
        raise ProxyProfileEligibilityError("Proxy username is required")
    if not profile.password_encrypted:
        raise ProxyProfileEligibilityError("Proxy password is required")
    try:
        password = _decrypt_password(profile, settings)
    except (InvalidToken, ValueError) as exc:
        raise ProxyProfileEligibilityError("Proxy password cannot be decrypted") from exc
    if not password:
        raise ProxyProfileEligibilityError("Proxy password is required")
    target_country_code = settings.vinted_target_country_code.strip().upper()
    if profile.country_code != target_country_code:
        raise ProxyProfileEligibilityError(
            f"Proxy profile country must match target country {target_country_code}"
        )


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


def _validate_accept_language(value: str, _locale: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Proxy accept_language cannot be empty")
    if not any(chunk.split(";", 1)[0].strip() for chunk in cleaned.split(",")):
        raise ValueError("Proxy accept_language must include at least one language tag")
    return cleaned


def _validate_screen(value: str) -> str:
    cleaned = value.strip().lower()
    if not SCREEN_PATTERN.match(cleaned):
        raise ValueError("Proxy screen must use WIDTHxHEIGHT format")
    return cleaned


def _validate_vinted_screen(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned != "catalog":
        raise ValueError("Proxy Vinted screen must be catalog")
    return cleaned


PROXY_CONTEXT_PRESETS: dict[str, ProxyContextPreset] = {
    "ES": _context_preset("ES", "es-ES", "en-GB,en;q=0.9", "1920x1080"),
    "FR": _context_preset("FR", "fr-FR", "fr-FR,fr;q=0.9,en;q=0.8", "1920x1080"),
    "IT": _context_preset("IT", "it-IT", "it-IT,it;q=0.9,en;q=0.8", "1920x1080"),
    "DE": _context_preset("DE", "de-DE", "de-DE,de;q=0.9,en;q=0.8", "1920x1080"),
    "PT": _context_preset("PT", "pt-PT", "pt-PT,pt;q=0.9,en;q=0.8", "1920x1080"),
    "NL": _context_preset("NL", "nl-NL", "nl-NL,nl;q=0.9,en;q=0.8", "1920x1080"),
    "BE": _context_preset("BE", "fr-BE", "fr-BE,fr;q=0.9,nl;q=0.8,en;q=0.7", "1920x1080"),
}


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


def _validate_sticky_username_template(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_STICKY_USERNAME_TEMPLATE_LENGTH:
        raise ValueError("Proxy sticky username template must contain between 1 and 255 characters")
    try:
        parsed = list(Formatter().parse(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Proxy sticky username template must be a valid format string") from exc
    fields = [field_name for _literal, field_name, _format_spec, _conversion in parsed if field_name is not None]
    has_unsupported_formatting = any(
        field_name is not None and (format_spec or conversion)
        for _literal, field_name, format_spec, conversion in parsed
    )
    if len(fields) != 2 or set(fields) != {"username", "session_id"} or has_unsupported_formatting:
        raise ValueError(
            "Proxy sticky username template must contain exactly plain {username} and {session_id} fields"
        )
    return value


def _validate_sticky_ttl_minutes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 120:
        raise ValueError("Proxy sticky TTL must be between 1 and 120 minutes")
    return value
