import json
import logging
import os
from functools import lru_cache

import jwt
from fastapi import Request, WebSocket
from pydantic import BaseModel, model_validator
from starlette.status import HTTP_401_UNAUTHORIZED
from usso.core import UserData
from usso.exceptions import USSOException

logger = logging.getLogger("usso")


class JWTSecret(BaseModel):
    jwk_url: str | None = None
    secret: str | None = None
    type: str = "RS256"
    header: dict[str, str] = {"type": "Cookie", "name": "usso_access_token"}

    def __hash__(self):
        return hash(self.model_dump_json())

    @model_validator(mode="before")
    def validate_secret(data: dict):
        if not data.get("jwk_url") and not data.get("secret"):
            raise ValueError("Either jwk_url or secret must be provided")
        return data

    @classmethod
    @lru_cache
    def get_jwk_keys(cls, jwk_url):
        return jwt.PyJWKClient(
            jwk_url,
            headers={
                "User-Agent": "usso-python",
            },
        )

    @lru_cache(maxsize=128)
    def decode(self, token: str):
        if self.jwk_url:
            jwk_client = self.get_jwk_keys(self.jwk_url)
            signing_key = jwk_client.get_signing_key_from_jwt(token)
            return jwt.decode(token, signing_key.key, algorithms=[self.type])

        return jwt.decode(token, self.secret, algorithms=[self.type])


class Usso:
    def __init__(self, jwt_secret: str | dict | JWTSecret | None = None):
        if jwt_secret is None:
            self.jwk_url = os.getenv("USSO_JWK_URL")
            return

        if isinstance(jwt_secret, str):
            jwt_secret = json.loads(jwt_secret)
        if isinstance(jwt_secret, dict):
            jwt_secret = JWTSecret(**jwt_secret)

        self.jwk_url = jwt_secret

    def get_authorization_scheme_param(
        self,
        authorization_header_value: str | None,
    ) -> tuple[str, str]:
        if not authorization_header_value:
            return "", ""
        scheme, _, param = authorization_header_value.partition(" ")
        return scheme, param

    def user_data_from_token(self, token: str, **kwargs) -> UserData | None:
        """Return the user associated with a token value."""
        try:
            decoded = self.jwk_url.decode(token)
            if decoded["token_type"] != "access":
                raise USSOException(
                    status_code=401,
                    error="invalid_token_type",
                )
            decoded["token"] = token
            return UserData(**decoded)
        except jwt.exceptions.ExpiredSignatureError:
            if kwargs.get("raise_exception", True):
                raise USSOException(status_code=401, error="expired_signature")
        except jwt.exceptions.InvalidSignatureError:
            if kwargs.get("raise_exception", True):
                raise USSOException(status_code=401, error="invalid_signature")
        except jwt.exceptions.InvalidAlgorithmError:
            if kwargs.get("raise_exception", True):
                raise USSOException(
                    status_code=401,
                    error="invalid_algorithm",
                )
        except jwt.exceptions.InvalidIssuedAtError:
            if kwargs.get("raise_exception", True):
                raise USSOException(
                    status_code=401,
                    error="invalid_issued_at",
                )
        except jwt.exceptions.InvalidTokenError:
            if kwargs.get("raise_exception", True):
                raise USSOException(
                    status_code=401,
                    error="invalid_token",
                )
        except jwt.exceptions.InvalidKeyError:
            if kwargs.get("raise_exception", True):
                raise USSOException(
                    status_code=401,
                    error="invalid_key",
                )
        except KeyError as e:
            if kwargs.get("raise_exception", True):
                raise USSOException(
                    status_code=401,
                    error="key_error",
                    message=str(e),
                )
        except USSOException as e:
            if kwargs.get("raise_exception", True):
                raise e
        except Exception as e:
            if kwargs.get("raise_exception", True):
                raise USSOException(
                    status_code=401,
                    error="error",
                    message=str(e),
                )
            logger.error(e)

    async def jwt_access_security(self, request: Request) -> UserData | None:
        """Return the user associated with a token value."""
        kwargs = {}
        authorization = request.headers.get("Authorization")
        if authorization:
            scheme, credentials = self.get_authorization_scheme_param(authorization)
            if scheme.lower() == "bearer":
                token = credentials
                return self.user_data_from_token(token, **kwargs)

        cookie_token = request.cookies.get("usso_access_token")
        if cookie_token:
            return self.user_data_from_token(cookie_token, **kwargs)

        if kwargs.get("raise_exception", True):
            raise USSOException(
                status_code=HTTP_401_UNAUTHORIZED,
                error="unauthorized",
            )
        return None

    async def jwt_access_security_ws(self, websocket: WebSocket) -> UserData | None:
        """Return the user associated with a token value."""
        kwargs = {}
        authorization = websocket.headers.get("Authorization")
        if authorization:
            scheme, credentials = self.get_authorization_scheme_param(authorization)
            if scheme.lower() == "bearer":
                token = credentials
                return self.user_data_from_token(token, **kwargs)

        cookie_token = websocket.cookies.get("usso_access_token")
        if cookie_token:
            return self.user_data_from_token(cookie_token, **kwargs)

        if kwargs.get("raise_exception", True):
            raise USSOException(
                status_code=HTTP_401_UNAUTHORIZED,
                error="unauthorized",
            )
        return None
