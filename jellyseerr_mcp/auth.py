from __future__ import annotations

from typing import Optional

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from .config import AppConfig


class EnvTokenVerifier(TokenVerifier):
    """Simple token verifier that accepts tokens from env list.

    This is not a full OAuth AS; it's a bearer token gate suitable for
    local development or simple deployments until an external issuer is used.
    """

    def __init__(self, allowed_tokens: list[str], scopes: Optional[list[str]] = None):
        self.allowed = set(allowed_tokens)
        self.scopes = scopes or []

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        if token in self.allowed:
            return AccessToken(token=token, client_id="env-bearer", scopes=self.scopes)
        return None


def build_auth(config: AppConfig) -> tuple[Optional[AuthSettings], Optional[TokenVerifier]]:
    if not config.auth_enabled:
        return None, None

    # Require issuer and RS URLs for proper metadata and validation
    if not config.auth_issuer_url or not config.auth_resource_server_url:
        raise RuntimeError(
            "AUTH_ENABLED=true requires AUTH_ISSUER_URL and AUTH_RESOURCE_SERVER_URL to be set"
        )

    auth_settings = AuthSettings(
        issuer_url=config.auth_issuer_url,
        resource_server_url=config.auth_resource_server_url,
        required_scopes=config.auth_required_scopes,
    )

    token_verifier: Optional[TokenVerifier] = None
    if config.auth_bearer_tokens:
        token_verifier = EnvTokenVerifier(config.auth_bearer_tokens, scopes=config.auth_required_scopes)

    return auth_settings, token_verifier

