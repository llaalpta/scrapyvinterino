from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.crypto import decrypt_text, encrypt_text, fingerprint_text, mask_text
from vinted_monitor.core.redaction import redact_sensitive_text
from vinted_monitor.db.models import ProxyProfile


class ProxyProfileNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class ProxyPublicFields:
    id: int
    name: str
    scheme: str
    host: str
    port: int
    username: str | None
    username_masked: str | None
    has_password: bool
    password_fingerprint: str | None
    is_active: bool
    last_test_status: str | None
    last_test_ip: str | None
    last_test_error: str | None


def list_proxy_profiles(db: Session) -> list[ProxyProfile]:
    return list(db.scalars(select(ProxyProfile).order_by(ProxyProfile.id.desc())))


def create_proxy_profile(
    db: Session,
    *,
    name: str,
    scheme: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    is_active: bool = True,
    settings: Settings | None = None,
) -> ProxyProfile:
    settings = settings or get_settings()
    profile = ProxyProfile(
        name=_validate_name(name),
        scheme=_validate_scheme(scheme),
        host=_validate_host(host),
        port=_validate_port(port),
        username=username.strip() if username else None,
        password_encrypted=_encrypt_password(password, settings) if password else None,
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
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    clear_password: bool = False,
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
    if is_active is not None:
        profile.is_active = is_active
    db.commit()
    db.refresh(profile)
    return profile


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
        host=profile.host,
        port=profile.port,
        username=profile.username,
        username_masked=mask_text(profile.username),
        has_password=bool(profile.password_encrypted),
        password_fingerprint=fingerprint_text(password) if password else None,
        is_active=profile.is_active,
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


def _validate_host(host: str) -> str:
    cleaned = host.strip()
    if not cleaned:
        raise ValueError("Proxy host cannot be empty")
    return cleaned


def _validate_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise ValueError("Proxy port must be between 1 and 65535")
    return port
