from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import User, UserSession

LOCAL_SESSION_COOKIE_NAME = "vinted_monitor_session"
LOCAL_CSRF_HEADER_NAME = "X-CSRF-Token"
LOCAL_SESSION_COOKIE_PATH = "/api"
MIN_LOCAL_PASSWORD_LENGTH = 12
MAX_LOCAL_PASSWORD_LENGTH = 128
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PASSWORD_HASH = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = _PASSWORD_HASH.hash(secrets.token_urlsafe(32))


class LocalAuthError(RuntimeError):
    pass


class LocalAuthenticationRequiredError(LocalAuthError):
    pass


class LocalCsrfError(LocalAuthError):
    pass


class LocalOriginError(LocalAuthError):
    pass


class LocalCredentialsError(LocalAuthError):
    pass


@dataclass(frozen=True)
class LocalUserPrincipal:
    session_id: int
    token_hash: str
    user_id: int
    email: str
    expires_at: datetime


@dataclass(frozen=True)
class LocalUserProvisioningResult:
    user_id: int
    email: str
    created: bool
    password_updated: bool
    reactivated: bool


@dataclass(frozen=True)
class LocalSessionGrant:
    raw_token: str
    expires_at: datetime
    principal: LocalUserPrincipal | None
    issued: bool


def normalize_local_email(email: str) -> str:
    normalized = email.strip().lower()
    if len(normalized) > 255 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Email must be a valid address of at most 255 characters")
    return normalized


def validate_local_password(password: str) -> str:
    if not MIN_LOCAL_PASSWORD_LENGTH <= len(password) <= MAX_LOCAL_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must contain between {MIN_LOCAL_PASSWORD_LENGTH} and {MAX_LOCAL_PASSWORD_LENGTH} characters"
        )
    return password


def create_local_user(db: Session, *, email: str, password: str) -> User:
    normalized_email = normalize_local_email(email)
    validated_password = validate_local_password(password)
    if db.scalar(select(User.id).where(User.email == normalized_email)) is not None:
        raise ValueError("A local user with that email already exists")
    user = User(
        email=normalized_email,
        password_hash=_PASSWORD_HASH.hash(validated_password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def ensure_local_user(db: Session, *, email: str, password: str) -> LocalUserProvisioningResult:
    normalized_email = normalize_local_email(email)
    validated_password = validate_local_password(password)
    user = db.scalar(select(User).where(User.email == normalized_email))
    if user is None:
        user = User(
            email=normalized_email,
            password_hash=_PASSWORD_HASH.hash(validated_password),
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return LocalUserProvisioningResult(
            user_id=user.id,
            email=user.email,
            created=True,
            password_updated=False,
            reactivated=False,
        )

    password_updated = not _PASSWORD_HASH.verify(validated_password, user.password_hash)
    reactivated = not user.is_active
    if password_updated:
        user.password_hash = _PASSWORD_HASH.hash(validated_password)
    if reactivated:
        user.is_active = True
    if password_updated or reactivated:
        db.commit()
        db.refresh(user)
    return LocalUserProvisioningResult(
        user_id=user.id,
        email=user.email,
        created=False,
        password_updated=password_updated,
        reactivated=reactivated,
    )


def bootstrap_local_session(
    db: Session,
    *,
    raw_token: str | None,
    settings: Settings,
    now: datetime | None = None,
) -> LocalSessionGrant:
    current_time = now or datetime.now(UTC)
    db.execute(
        delete(UserSession).where(
            UserSession.user_id.is_(None),
            (UserSession.expires_at <= current_time) | UserSession.revoked_at.is_not(None),
        )
    )
    if raw_token:
        session = _session_for_token(db, raw_token, for_update=True)
        if session is not None and session.revoked_at is None and session.expires_at > current_time:
            principal = _principal_for_session(db, session)
            if session.user_id is None or principal is not None:
                db.commit()
                return LocalSessionGrant(
                    raw_token=raw_token,
                    expires_at=session.expires_at,
                    principal=principal,
                    issued=False,
                )
            session.revoked_at = current_time

    new_raw_token, token_hash = _new_session_token()
    expires_at = current_time + timedelta(minutes=settings.local_auth_preauth_ttl_minutes)
    db.add(UserSession(token_hash=token_hash, expires_at=expires_at))
    db.commit()
    return LocalSessionGrant(
        raw_token=new_raw_token,
        expires_at=expires_at,
        principal=None,
        issued=True,
    )


def login_local_user(
    db: Session,
    *,
    raw_token: str | None,
    csrf_token: str | None,
    email: str,
    password: str,
    settings: Settings,
    now: datetime | None = None,
) -> LocalSessionGrant:
    current_time = now or datetime.now(UTC)
    session = _valid_session_for_token(db, raw_token, now=current_time, for_update=True)
    if session is None:
        db.rollback()
        raise LocalAuthenticationRequiredError("Authentication bootstrap required")
    require_valid_csrf(raw_token, csrf_token, settings)

    try:
        normalized_email = normalize_local_email(email)
    except ValueError:
        normalized_email = ""
    password_has_valid_length = MIN_LOCAL_PASSWORD_LENGTH <= len(password) <= MAX_LOCAL_PASSWORD_LENGTH
    verification_password = password if password_has_valid_length else ""
    user = db.scalar(select(User).where(User.email == normalized_email)) if normalized_email else None
    stored_hash = user.password_hash if user is not None and user.is_active else _DUMMY_PASSWORD_HASH
    try:
        password_matches = _PASSWORD_HASH.verify(verification_password, stored_hash)
    except Exception:
        password_matches = False
    if user is None or not user.is_active or not password_has_valid_length or not password_matches:
        db.rollback()
        raise LocalCredentialsError("Invalid email or password")

    session.revoked_at = current_time
    new_raw_token, token_hash = _new_session_token()
    expires_at = current_time + timedelta(hours=settings.local_auth_session_ttl_hours)
    authenticated = UserSession(
        token_hash=token_hash,
        user_id=user.id,
        expires_at=expires_at,
        authenticated_at=current_time,
    )
    db.add(authenticated)
    db.commit()
    db.refresh(authenticated)
    return LocalSessionGrant(
        raw_token=new_raw_token,
        expires_at=expires_at,
        principal=LocalUserPrincipal(
            session_id=authenticated.id,
            token_hash=authenticated.token_hash,
            user_id=user.id,
            email=user.email,
            expires_at=authenticated.expires_at,
        ),
        issued=True,
    )


def authenticate_local_session(
    db: Session,
    *,
    raw_token: str | None,
    now: datetime | None = None,
) -> LocalUserPrincipal | None:
    session = _valid_session_for_token(db, raw_token, now=now or datetime.now(UTC))
    if session is None:
        return None
    return _principal_for_session(db, session)


def revoke_local_session(
    db: Session,
    *,
    raw_token: str | None,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(UTC)
    session = _valid_session_for_token(db, raw_token, now=current_time, for_update=True)
    if session is None or _principal_for_session(db, session) is None:
        db.rollback()
        raise LocalAuthenticationRequiredError("Authentication required")
    session.revoked_at = current_time
    db.commit()


def local_session_hash_is_active(db: Session, token_hash: str, *, now: datetime | None = None) -> bool:
    current_time = now or datetime.now(UTC)
    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == token_hash,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > current_time,
            UserSession.user_id.is_not(None),
            UserSession.authenticated_at.is_not(None),
        )
    )
    return session is not None and _principal_for_session(db, session) is not None


def csrf_token_for_session(raw_token: str, settings: Settings) -> str:
    return hmac.new(
        settings.app_secret_key.encode("utf-8"),
        f"local-csrf:{raw_token}".encode(),
        hashlib.sha256,
    ).hexdigest()


def require_valid_csrf(raw_token: str | None, provided_token: str | None, settings: Settings) -> None:
    if not raw_token or not provided_token:
        raise LocalCsrfError("CSRF validation failed")
    expected = csrf_token_for_session(raw_token, settings)
    if not hmac.compare_digest(expected, provided_token):
        raise LocalCsrfError("CSRF validation failed")


def require_trusted_origin(origin: str | None, settings: Settings) -> None:
    if origin is None or origin not in settings.cors_origins:
        raise LocalOriginError("Origin validation failed")


def local_session_cookie_secure(settings: Settings) -> bool:
    return settings.app_env.strip().lower() not in {"development", "test"}


def _valid_session_for_token(
    db: Session,
    raw_token: str | None,
    *,
    now: datetime,
    for_update: bool = False,
) -> UserSession | None:
    if not raw_token:
        return None
    session = _session_for_token(db, raw_token, for_update=for_update)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        return None
    return session


def _session_for_token(db: Session, raw_token: str, *, for_update: bool = False) -> UserSession | None:
    statement = select(UserSession).where(UserSession.token_hash == _hash_session_token(raw_token))
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _principal_for_session(db: Session, session: UserSession) -> LocalUserPrincipal | None:
    if session.user_id is None or session.authenticated_at is None:
        return None
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return LocalUserPrincipal(
        session_id=session.id,
        token_hash=session.token_hash,
        user_id=user.id,
        email=user.email,
        expires_at=session.expires_at,
    )


def _new_session_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    return raw_token, _hash_session_token(raw_token)


def _hash_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()
