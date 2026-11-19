"""Tiny async client for the *arr APIs (Radarr/Sonarr v3, Prowlarr v1)."""
import httpx

from . import settings


class ArrError(Exception):
    pass


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5))
    return _client


class Arr:
    def __init__(self, name: str, base_url: str, api_version: str):
        self.name = name
        self.base = f"{base_url.rstrip('/')}/api/{api_version}"

    def _headers(self) -> dict:
        try:
            key = settings.api_keys()[self.name]
        except (OSError, KeyError) as e:
            raise ArrError(f"{self.name}: API key not available yet") from e
        return {"X-Api-Key": key}

    async def request(self, method: str, path: str, *, params=None, json=None):
        try:
            r = await client().request(
                method, self.base + path, params=params, json=json, headers=self._headers()
            )
        except httpx.HTTPError as e:
            raise ArrError(f"{self.name} is not reachable ({e.__class__.__name__})") from e
        if r.status_code >= 400:
            detail = r.text[:300]
            try:
                body = r.json()
                if isinstance(body, list) and body and "errorMessage" in body[0]:
                    detail = "; ".join(x["errorMessage"] for x in body)
                elif isinstance(body, dict) and "message" in body:
                    detail = body["message"]
            except ValueError:
                pass
            raise ArrError(f"{self.name} {method} {path} -> {r.status_code}: {detail}")
        if not r.content:
            return None
        return r.json()

    async def get(self, path: str, **params):
        return await self.request("GET", path, params=params or None)

    async def post(self, path: str, body, **params):
        return await self.request("POST", path, params=params or None, json=body)


radarr = Arr("radarr", settings.RADARR_URL, "v3")
sonarr = Arr("sonarr", settings.SONARR_URL, "v3")
prowlarr = Arr("prowlarr", settings.PROWLARR_URL, "v1")


def set_fields(item: dict, values: dict) -> None:
    """Fill a provider schema's `fields` list by field name."""
    for f in item.get("fields", []):
        if f.get("name") in values:
            f["value"] = values[f["name"]]


def field(item: dict, name: str):
    for f in item.get("fields", []):
        if f.get("name") == name:
            return f.get("value")
    return None


def poster(item: dict) -> str | None:
    for img in item.get("images") or []:
        if img.get("coverType") == "poster":
            return img.get("remoteUrl") or img.get("url")
    return None
