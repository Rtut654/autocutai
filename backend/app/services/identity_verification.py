"""Verify Google and Apple sign-in tokens.

The clients send the provider's ID token. Everything that identifies the user
- subject, email, whether the email is verified - is taken from the token's
signed claims, never from the request body. Anything else in the body (a
display name, an avatar URL) is cosmetic.

Before this existed, the login endpoints accepted `email` and
`provider_user_id` straight from the request, so anyone could sign in as
anyone by posting their address.

Configuration:
    GOOGLE_CLIENT_IDS   comma-separated OAuth client IDs whose tokens we accept
                        (iOS, web and Android each have their own)
    APPLE_AUDIENCES     comma-separated audiences for Sign in with Apple: the
                        iOS bundle ID, plus a Services ID if web uses Apple
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

import jwt

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"

DEFAULT_APPLE_AUDIENCES = ("com.bestshotai.app",)

# Allowed clock skew between us and the provider, in seconds.
LEEWAY_SECONDS = 60


class IdentityVerificationError(ValueError):
    """The token could not be verified. The message is safe to show."""


@dataclass(frozen=True)
class VerifiedIdentity:
    provider: str
    subject: str
    email: Optional[str]
    email_verified: bool
    name: Optional[str] = None
    picture: Optional[str] = None


def _csv_env(name: str, default: Sequence[str] = ()) -> List[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _truthy(value: Any) -> bool:
    # Apple sends booleans as strings ("true"); Google sends real booleans.
    return value is True or str(value).lower() == "true"


# A key resolver takes the raw token and returns the key that signed it.
KeyResolver = Callable[[str], Any]


def _jwks_resolver(url: str) -> KeyResolver:
    client = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)

    def resolve(token: str) -> Any:
        try:
            return client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError as exc:
            raise IdentityVerificationError("Could not fetch the provider's signing keys.") from exc

    return resolve


class IdentityVerifier:
    """Checks provider tokens. Key resolvers are injectable for tests."""

    def __init__(
        self,
        *,
        google_client_ids: Optional[Sequence[str]] = None,
        apple_audiences: Optional[Sequence[str]] = None,
        google_key_resolver: Optional[KeyResolver] = None,
        apple_key_resolver: Optional[KeyResolver] = None,
    ) -> None:
        self._google_client_ids = list(google_client_ids) if google_client_ids is not None else None
        self._apple_audiences = list(apple_audiences) if apple_audiences is not None else None
        self._google_key_resolver = google_key_resolver
        self._apple_key_resolver = apple_key_resolver

    # Read lazily so a changed environment (or a test) takes effect.
    @property
    def google_client_ids(self) -> List[str]:
        if self._google_client_ids is not None:
            return self._google_client_ids
        return _csv_env("GOOGLE_CLIENT_IDS")

    @property
    def apple_audiences(self) -> List[str]:
        if self._apple_audiences is not None:
            return self._apple_audiences
        return _csv_env("APPLE_AUDIENCES", DEFAULT_APPLE_AUDIENCES)

    def _google_keys(self) -> KeyResolver:
        if self._google_key_resolver is None:
            self._google_key_resolver = _jwks_resolver(GOOGLE_JWKS_URL)
        return self._google_key_resolver

    def _apple_keys(self) -> KeyResolver:
        if self._apple_key_resolver is None:
            self._apple_key_resolver = _jwks_resolver(APPLE_JWKS_URL)
        return self._apple_key_resolver

    def _decode(
        self,
        token: str,
        *,
        key_resolver: KeyResolver,
        audiences: Sequence[str],
        issuers: Sequence[str],
        provider_label: str,
    ) -> Dict[str, Any]:
        if not token or token.count(".") != 2:
            raise IdentityVerificationError(f"Missing or malformed {provider_label} token.")
        if not audiences:
            raise IdentityVerificationError(f"{provider_label} sign-in is not configured on this server.")

        key = key_resolver(token)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=list(audiences),
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                leeway=LEEWAY_SECONDS,
            )
        except jwt.ExpiredSignatureError as exc:
            raise IdentityVerificationError(f"The {provider_label} sign-in has expired. Try again.") from exc
        except jwt.InvalidAudienceError as exc:
            raise IdentityVerificationError(f"This {provider_label} token was issued for a different app.") from exc
        except jwt.PyJWTError as exc:
            raise IdentityVerificationError(f"Could not verify the {provider_label} token.") from exc

        if claims.get("iss") not in issuers:
            raise IdentityVerificationError(f"The {provider_label} token has an unexpected issuer.")
        if not str(claims.get("sub") or "").strip():
            raise IdentityVerificationError(f"The {provider_label} token has no subject.")
        return claims

    def verify_google(self, id_token: str) -> VerifiedIdentity:
        claims = self._decode(
            id_token,
            key_resolver=self._google_keys(),
            audiences=self.google_client_ids,
            issuers=GOOGLE_ISSUERS,
            provider_label="Google",
        )
        return VerifiedIdentity(
            provider="google",
            subject=str(claims["sub"]),
            email=(str(claims["email"]).lower().strip() if claims.get("email") else None),
            email_verified=_truthy(claims.get("email_verified")),
            name=claims.get("name"),
            picture=claims.get("picture"),
        )

    def verify_apple(self, identity_token: str) -> VerifiedIdentity:
        claims = self._decode(
            identity_token,
            key_resolver=self._apple_keys(),
            audiences=self.apple_audiences,
            issuers=(APPLE_ISSUER,),
            provider_label="Apple",
        )
        # Apple only includes the name in the app-side credential, never in
        # the token, so the caller supplies it separately as a display value.
        return VerifiedIdentity(
            provider="apple",
            subject=str(claims["sub"]),
            email=(str(claims["email"]).lower().strip() if claims.get("email") else None),
            email_verified=_truthy(claims.get("email_verified")),
        )


identity_verifier = IdentityVerifier()
