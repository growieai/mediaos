"""Instagram Login transport only; callers own tenant, approval and replay guards."""

import re
from typing import Any, TypeVar
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

from .http import (
    Response,
    SocialProviderError,
    Transport,
    canonical_hash,
    credential,
    identifier,
    sanitize,
)
from .schemas import (
    Comment,
    ContainerStatus,
    CreatedObject,
    Insights,
    InstagramSettings,
    Media,
    MetricName,
    Observation,
    Page,
    Profile,
    Scope,
    TokenGrant,
    public_https,
)

T = TypeVar("T", bound=BaseModel)
MEDIA_FIELDS = "id,media_type,caption,permalink,timestamp,username,owner,like_count,comments_count"
COMMENT_FIELDS = "id,text,timestamp,username,parent_id,media"
METRIC_FIELDS = ("reach", "saved", "shares", "comments", "follows", "views", "likes")


def parse(schema: type[T], value: Any, *, mutation: bool = False) -> T:
    try:
        return schema.model_validate(value)
    except (ValueError, TypeError, ValidationError):
        raise SocialProviderError("UNKNOWN_OUTCOME" if mutation else "INVALID_OUTPUT") from None


def bounded_text(value: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise SocialProviderError("INVALID_REQUEST")
    return value


class InstagramProvider:
    def __init__(
        self,
        settings: InstagramSettings,
        token: SecretStr | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings, self.token = settings, token
        self.http = Transport(transport)

    def authorization_url(self, state: str, scopes: tuple[Scope, ...]) -> str:
        if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{32,1024}", state):
            raise SocialProviderError("INVALID_REQUEST")
        try:
            TypeAdapter(tuple[Scope, ...]).validate_python(scopes, strict=True)
        except ValidationError:
            raise SocialProviderError("INVALID_REQUEST") from None
        if (
            not scopes
            or len(set(scopes)) != len(scopes)
            or "instagram_business_basic" not in scopes
        ):
            raise SocialProviderError("INVALID_REQUEST")
        return "https://www.instagram.com/oauth/authorize?" + urlencode(
            {
                "client_id": self.settings.app_id,
                "redirect_uri": self.settings.redirect_uri,
                "response_type": "code",
                "scope": ",".join(scopes),
                "state": state,
                "enable_fb_login": "0",
                "force_authentication": "1",
            }
        )

    def _token_grant(self, response: Response) -> TokenGrant:
        payload = response.payload
        # Accept only the two explicitly supported wire shapes; never search arbitrary nesting.
        if "data" in payload:
            data = payload["data"]
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
                raise SocialProviderError("UNKNOWN_OUTCOME")
            payload = data[0]
        user_id = payload.get("user_id")
        if type(user_id) is int and user_id > 0:
            user_id = str(user_id)
        raw_token = payload.get("access_token")
        if not isinstance(raw_token, str):
            raise SocialProviderError("UNKNOWN_OUTCOME")
        try:
            token = SecretStr(raw_token)
            credential(token)
            return TokenGrant(
                access_token=token,
                user_id=user_id,
                expires_in=payload.get("expires_in"),
                token_type=payload.get("token_type"),
                permissions=payload.get("permissions", []),
                response_hash=response.response_hash,
                request_id=response.request_id,
            )
        except (ValueError, TypeError, SocialProviderError):
            raise SocialProviderError("UNKNOWN_OUTCOME") from None

    def exchange_code(self, code: SecretStr) -> TokenGrant:
        secret, value = credential(self.settings.app_secret), credential(code)
        response = self.http.request(
            "POST",
            "api.instagram.com",
            "/oauth/access_token",
            form={
                "client_id": self.settings.app_id,
                "client_secret": secret,
                "grant_type": "authorization_code",
                "redirect_uri": self.settings.redirect_uri,
                "code": value,
            },
            secrets=(secret, value),
        )
        return self._token_grant(response)

    def exchange_long_lived(self, token: SecretStr) -> TokenGrant:
        secret, value = credential(self.settings.app_secret), credential(token)
        return self._token_grant(
            self.http.request(
                "GET",
                "graph.instagram.com",
                "/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": secret,
                    "access_token": value,
                },
                secrets=(secret, value),
            )
        )

    def refresh_token(self, token: SecretStr) -> TokenGrant:
        value = credential(token)
        return self._token_grant(
            self.http.request(
                "GET",
                "graph.instagram.com",
                "/refresh_access_token",
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": value,
                },
                secrets=(value,),
            )
        )

    def _request(self, method: str, path: str, values: dict[str, str] | None = None) -> Response:
        if self.token is None:
            raise SocialProviderError("AUTHENTICATION")
        secrets = (credential(self.settings.app_secret), credential(self.token))
        response = self.http.request(
            method,
            "graph.instagram.com",
            f"/{self.settings.api_version}{path}",
            token=self.token,
            params=values if method == "GET" else None,
            form=values if method == "POST" else None,
            safe_read=method == "GET",
            secrets=secrets,
        )
        try:
            redacted = sanitize(response.payload, secrets)
            response.redacted = redacted != response.payload
            response.payload = redacted
        except (TypeError, ValueError, RecursionError):
            raise SocialProviderError(
                "INVALID_OUTPUT" if method == "GET" else "UNKNOWN_OUTCOME"
            ) from None
        return response

    def _observed(self, path: str, response: Response, payload: T) -> Observation[T]:
        secrets: tuple[str, ...] = (credential(self.settings.app_secret),)
        if self.token is not None:
            secrets += (credential(self.token),)
        try:
            raw = sanitize(response.payload, secrets)
            raw_hash = canonical_hash(raw)
            redacted = response.redacted or raw != response.payload
        except (ValueError, TypeError, RecursionError):
            raise SocialProviderError("INVALID_OUTPUT") from None
        return Observation(
            api_version=self.settings.api_version,
            endpoint=f"/{self.settings.api_version}{path}",
            captured_at=response.captured_at,
            payload=payload,
            raw=raw,
            raw_hash=raw_hash,
            response_hash=response.response_hash,
            redacted=redacted,
            request_id=response.request_id,
        )

    def profile(self) -> Observation[Profile]:
        path = "/me"
        response = self._request("GET", path, {"fields": "id,user_id,username,account_type"})
        payload = response.payload
        user_id = payload.get("user_id", payload.get("id"))
        if type(user_id) is int and user_id > 0:
            user_id = str(user_id)
        profile = parse(
            Profile,
            {
                "id": user_id,
                "username": payload.get("username"),
                "account_type": payload.get("account_type"),
            },
        )
        return self._observed(path, response, profile)

    def _image_url(self, value: str) -> str:
        try:
            host = urlsplit(public_https(value)).hostname
        except (ValueError, TypeError):
            raise SocialProviderError("INVALID_REQUEST") from None
        if host not in self.settings.media_host_allowlist:
            raise SocialProviderError("INVALID_REQUEST")
        return value

    def create_image(
        self,
        user_id: str,
        image_url: str,
        caption: str | None = None,
        is_carousel_item: bool = False,
    ) -> Observation[CreatedObject]:
        if type(is_carousel_item) is not bool or (is_carousel_item and caption is not None):
            raise SocialProviderError("INVALID_REQUEST")
        values = {"image_url": self._image_url(image_url)}
        if is_carousel_item:
            values["is_carousel_item"] = "true"
        else:
            # AI influencer content is disclosed on the single image or carousel parent.
            values["is_ai_generated"] = "true"
        if caption is not None:
            values["caption"] = bounded_text(caption, 2200)
        return self._create(f"/{identifier(user_id)}/media", values)

    def create_image_child(self, user_id: str, image_url: str) -> Observation[CreatedObject]:
        return self.create_image(user_id, image_url, is_carousel_item=True)

    def create_carousel(
        self,
        user_id: str,
        children: list[str],
        caption: str,
    ) -> Observation[CreatedObject]:
        if not isinstance(children, list) or not 2 <= len(children) <= 10:
            raise SocialProviderError("INVALID_REQUEST")
        ids = [identifier(value) for value in children]
        if len(set(ids)) != len(ids):
            raise SocialProviderError("INVALID_REQUEST")
        return self._create(
            f"/{identifier(user_id)}/media",
            {
                "media_type": "CAROUSEL",
                "is_ai_generated": "true",
                "children": ",".join(ids),
                "caption": bounded_text(caption, 2200),
            },
        )

    def _create(self, path: str, values: dict[str, str]) -> Observation[CreatedObject]:
        response = self._request("POST", path, values)
        payload = parse(CreatedObject, response.payload, mutation=True)
        try:
            return self._observed(path, response, payload)
        except SocialProviderError:
            raise SocialProviderError("UNKNOWN_OUTCOME") from None

    def container_status(self, container_id: str) -> Observation[ContainerStatus]:
        path = f"/{identifier(container_id)}"
        response = self._request("GET", path, {"fields": "id,status_code,status"})
        payload = parse(ContainerStatus, response.payload)
        if payload.id != container_id:
            raise SocialProviderError("INVALID_OUTPUT")
        return self._observed(path, response, payload)

    def publish(self, user_id: str, container_id: str) -> Observation[CreatedObject]:
        return self._create(
            f"/{identifier(user_id)}/media_publish", {"creation_id": identifier(container_id)}
        )

    def read_media(self, media_id: str) -> Observation[Media]:
        path = f"/{identifier(media_id)}"
        response = self._request("GET", path, {"fields": MEDIA_FIELDS})
        payload = parse(Media, response.payload)
        if payload.id != media_id:
            raise SocialProviderError("INVALID_OUTPUT")
        return self._observed(path, response, payload)

    def _page(
        self,
        path: str,
        schema: type[T],
        fields: str,
        after: str | None,
    ) -> Observation[Page[T]]:
        values = {"fields": fields, "limit": "50"}
        if after is not None:
            if not isinstance(after, str) or not re.fullmatch(r"[A-Za-z0-9_+=/.-]{1,4096}", after):
                raise SocialProviderError("INVALID_REQUEST")
            values["after"] = after
        response = self._request("GET", path, values)
        data = response.payload.get("data")
        paging = response.payload.get("paging", {})
        if not isinstance(data, list) or len(data) > 100 or not isinstance(paging, dict):
            raise SocialProviderError("INVALID_OUTPUT")
        cursors = paging.get("cursors", {})
        if not isinstance(cursors, dict):
            raise SocialProviderError("INVALID_OUTPUT")
        cursor = cursors.get("after")
        if cursor is not None and (
            not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_+=/.-]{1,4096}", cursor)
        ):
            raise SocialProviderError("INVALID_OUTPUT")
        has_more = bool(paging.get("next"))
        if has_more and cursor is None:
            raise SocialProviderError("INVALID_OUTPUT")
        payload = Page(
            data=[parse(schema, value) for value in data], after=cursor, has_more=has_more
        )
        return self._observed(path, response, payload)

    def list_media(self, user_id: str, after: str | None = None) -> Observation[Page[Media]]:
        return self._page(f"/{identifier(user_id)}/media", Media, MEDIA_FIELDS, after)

    def insights(
        self,
        media_id: str,
        metrics: tuple[MetricName, ...] = ("reach", "saved", "shares", "comments"),
    ) -> Observation[Insights]:
        try:
            TypeAdapter(tuple[MetricName, ...]).validate_python(metrics, strict=True)
        except ValidationError:
            raise SocialProviderError("INVALID_REQUEST") from None
        if not metrics or len(set(metrics)) != len(metrics):
            raise SocialProviderError("INVALID_REQUEST")
        path = f"/{identifier(media_id)}/insights"
        response = self._request("GET", path, {"metric": ",".join(metrics)})
        data = response.payload.get("data")
        if not isinstance(data, list) or len(data) > 50:
            raise SocialProviderError("INVALID_OUTPUT")
        values: dict[str, int | None] = {name: None for name in METRIC_FIELDS}
        # A definition describes the metric, not whether this particular sample
        # is available. Stable definitions allow safe null-to-observed comparisons.
        definitions = {
            name: f"Meta media {name}; lifetime scalar; API {self.settings.api_version}"
            for name in METRIC_FIELDS
        }
        observed = set()
        for item in data:
            if not isinstance(item, dict) or item.get("name") not in metrics:
                raise SocialProviderError("INVALID_OUTPUT")
            name = item["name"]
            if name in observed:
                raise SocialProviderError("INVALID_OUTPUT")
            observed.add(name)
            record_id = item.get("id")
            if record_id is not None and (
                not isinstance(record_id, str) or not record_id.startswith(media_id + "/insights/")
            ):
                raise SocialProviderError("INVALID_OUTPUT")
            if item.get("period") != "lifetime":
                continue
            series, total = item.get("values"), item.get("total_value")
            count = None
            if series is not None:
                if not isinstance(series, list):
                    raise SocialProviderError("INVALID_OUTPUT")
                if len(series) == 1 and isinstance(series[0], dict):
                    count = series[0].get("value")
            if total is not None:
                if not isinstance(total, dict):
                    raise SocialProviderError("INVALID_OUTPUT")
                total_count = total.get("value")
                if count is not None and count != total_count:
                    raise SocialProviderError("INVALID_OUTPUT")
                count = total_count
            if count is not None and (type(count) is not int or not 0 <= count <= 9007199254740991):
                raise SocialProviderError("INVALID_OUTPUT")
            values[name] = count
        payload = Insights(media_id=media_id, metrics=values, definitions=definitions)
        return self._observed(path, response, payload)

    def read_comment(self, comment_id: str) -> Observation[Comment]:
        path = f"/{identifier(comment_id)}"
        response = self._request("GET", path, {"fields": COMMENT_FIELDS})
        payload = parse(Comment, response.payload)
        if payload.id != comment_id:
            raise SocialProviderError("INVALID_OUTPUT")
        return self._observed(path, response, payload)

    def list_comments(self, media_id: str, after: str | None = None) -> Observation[Page[Comment]]:
        return self._page(f"/{identifier(media_id)}/comments", Comment, COMMENT_FIELDS, after)

    def list_replies(self, comment_id: str, after: str | None = None) -> Observation[Page[Comment]]:
        return self._page(f"/{identifier(comment_id)}/replies", Comment, COMMENT_FIELDS, after)

    def reply(self, comment_id: str, exact_approved_text: str) -> Observation[CreatedObject]:
        """Caller must hold its durable exact-text/identity approval guard before calling."""
        return self._create(
            f"/{identifier(comment_id)}/replies",
            {
                "message": bounded_text(exact_approved_text, 2200),
            },
        )
