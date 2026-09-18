import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .repository import Repository
from .settings import Settings

bearer = HTTPBearer(auto_error=False)
bearer_dependency = Depends(bearer)


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


# Input: Operator cookie secret and an optional validity period.
# Output: A signed cookie value containing its expiry time.
def make_operator_cookie(settings: Settings, hours: int = 12) -> str:
    expires = int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp())
    value = f"{expires}.{secrets.token_hex(16)}"
    return f"{value}.{_sign(value, settings.cookie_secret)}"


def operator_required(
    request: Request,
    operator_session: str | None = Cookie(None),
) -> str:
    settings: Settings = request.app.state.settings
    if not operator_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "operator login required")
    try:
        expires, nonce, signature = operator_session.split(".", 2)
        value = f"{expires}.{nonce}"
        valid = hmac.compare_digest(signature, _sign(value, settings.cookie_secret))
        fresh = int(expires) > int(datetime.now(timezone.utc).timestamp())
    except (ValueError, TypeError):
        valid = fresh = False
    if not valid or not fresh:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "operator login required")
    return _sign(operator_session, settings.cookie_secret)


def operator_api_required(
    request: Request,
    x_operator_password: str | None = Header(None),
    x_csrf_token: str | None = Header(None),
    operator_session: str | None = Cookie(None),
) -> None:
    settings: Settings = request.app.state.settings
    password_ok = bool(
        x_operator_password
        and hmac.compare_digest(x_operator_password, settings.operator_password)
    )
    if password_ok:
        return
    csrf = operator_required(request, operator_session)
    if request.method != "GET" and not hmac.compare_digest(x_csrf_token or "", csrf):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid CSRF token")


def app_required(
    request: Request,
    app_id: str,
    credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
) -> None:
    repo: Repository = request.app.state.repo
    valid = bool(
        credentials
        and credentials.scheme.lower() == "bearer"
        and repo.app_token_valid(app_id, credentials.credentials)
    )
    if not valid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid app token")
