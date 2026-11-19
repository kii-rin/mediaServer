"""Wires the apps together. Runs at every start and only adds what's
missing, so it's safe to re-run and it repairs anything a user deleted."""
import asyncio
import logging
import time

import yaml

from . import settings
from .arr import Arr, ArrError, field, prowlarr, radarr, set_fields, sonarr

log = logging.getLogger("mediabox.setup")


class SetupState:
    def __init__(self):
        self.state = "starting"  # starting | waiting | configuring | ready | error
        self.message = "Starting up"
        self.steps: dict[str, str] = {}

    def as_dict(self):
        return {"state": self.state, "message": self.message, "steps": self.steps}


status = SetupState()


async def wait_for(arr: Arr, timeout: float = 900) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            await arr.get("/system/status")
            return
        except ArrError as e:
            if time.monotonic() > deadline:
                raise ArrError(f"{arr.name} did not start in time: {e}")
            await asyncio.sleep(3)


async def schema_item(arr: Arr, path: str, implementation: str) -> dict:
    for item in await arr.get(f"{path}/schema"):
        if item.get("implementation") == implementation:
            return item
    raise ArrError(f"{arr.name}: no '{implementation}' in {path}/schema")


async def ensure_root_folder(arr: Arr, path: str) -> None:
    folders = await arr.get("/rootfolder")
    if not any(f.get("path", "").rstrip("/") == path for f in folders):
        await arr.post("/rootfolder", {"path": path})


async def ensure_qbittorrent(arr: Arr, category_field: str, category: str) -> None:
    clients = await arr.get("/downloadclient")
    if any(c.get("implementation") == "QBittorrent" for c in clients):
        return
    item = await schema_item(arr, "/downloadclient", "QBittorrent")
    item.update(name="qBittorrent", enable=True, priority=1)
    set_fields(item, {
        "host": settings.QBIT_HOST,
        "port": settings.QBIT_PORT,
        "useSsl": False,
        "urlBase": "",
        "username": "",
        "password": "",
        category_field: category,
    })
    await arr.post("/downloadclient", item, forceSave="true")


async def ensure_prowlarr_app(implementation: str, base_url: str, api_key: str) -> None:
    apps = await prowlarr.get("/applications")
    if any(a.get("implementation") == implementation for a in apps):
        return
    item = await schema_item(prowlarr, "/applications", implementation)
    item.update(name=implementation, syncLevel="fullSync")
    set_fields(item, {
        "prowlarrUrl": settings.PROWLARR_URL,
        "baseUrl": base_url,
        "apiKey": api_key,
    })
    await prowlarr.post("/applications", item, forceSave="true")


async def ensure_tag(label: str) -> int:
    for t in await prowlarr.get("/tag"):
        if t.get("label") == label:
            return t["id"]
    return (await prowlarr.post("/tag", {"label": label}))["id"]


async def ensure_flaresolverr(tag_id: int) -> None:
    proxies = await prowlarr.get("/indexerproxy")
    if any(p.get("implementation") == "FlareSolverr" for p in proxies):
        return
    item = await schema_item(prowlarr, "/indexerproxy", "FlareSolverr")
    item.update(name="FlareSolverr", tags=[tag_id])
    set_fields(item, {"host": settings.FLARESOLVERR_URL, "requestTimeout": 60})
    await prowlarr.post("/indexerproxy", item, forceSave="true")


def load_indexer_list() -> list[dict]:
    try:
        with open(settings.INDEXERS_FILE) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return []
    entries = data.get("indexers") or []
    return [e for e in entries if isinstance(e, dict) and e.get("definition")]


async def ensure_indexers(flare_tag: int) -> list[str]:
    wanted = load_indexer_list()
    if not wanted:
        return []
    existing = {i.get("definitionName") for i in await prowlarr.get("/indexer")}
    missing = [w for w in wanted if w["definition"] not in existing]
    problems = []
    if not missing:
        return problems

    schema = {s.get("definitionName"): s for s in await prowlarr.get("/indexer/schema")}
    profiles = await prowlarr.get("/appprofile")
    profile_id = profiles[0]["id"] if profiles else 1

    for w in missing:
        item = schema.get(w["definition"])
        if item is None:
            problems.append(f"unknown indexer '{w['definition']}'")
            continue
        item.update(
            name=w.get("name") or item.get("name"),
            enable=True,
            appProfileId=profile_id,
            priority=25,
            tags=[flare_tag] if w.get("flaresolverr") else [],
        )
        if not field(item, "baseUrl") and item.get("indexerUrls"):
            set_fields(item, {"baseUrl": item["indexerUrls"][0]})
        set_fields(item, w.get("settings") or {})
        try:
            await prowlarr.post("/indexer", item, forceSave="true")
        except ArrError as e:
            problems.append(f"{w['definition']}: {e}")
    return problems


async def run_once() -> None:
    keys = settings.api_keys()

    status.state, status.message = "waiting", "Waiting for apps to start"
    for arr in (radarr, sonarr, prowlarr):
        await wait_for(arr)
        status.steps[f"{arr.name} running"] = "ok"

    status.state, status.message = "configuring", "Connecting apps"

    async def step(name, coro):
        await coro
        status.steps[name] = "ok"

    await step("Radarr movies folder", ensure_root_folder(radarr, settings.MOVIES_ROOT))
    await step("Sonarr TV folder", ensure_root_folder(sonarr, settings.TV_ROOT))
    await step("Radarr → qBittorrent", ensure_qbittorrent(radarr, "movieCategory", "radarr"))
    await step("Sonarr → qBittorrent", ensure_qbittorrent(sonarr, "tvCategory", "sonarr"))
    await step("Prowlarr → Radarr", ensure_prowlarr_app("Radarr", settings.RADARR_URL, keys["radarr"]))
    await step("Prowlarr → Sonarr", ensure_prowlarr_app("Sonarr", settings.SONARR_URL, keys["sonarr"]))
    tag = await ensure_tag("flaresolverr")
    await step("FlareSolverr", ensure_flaresolverr(tag))

    problems = await ensure_indexers(tag)
    status.steps["Indexers"] = "ok" if not problems else "warning: " + "; ".join(problems)
    # Push indexers to Radarr/Sonarr right away instead of waiting for the timer
    await prowlarr.post("/command", {"name": "ApplicationIndexerSync"})

    status.state = "ready"
    status.message = "Ready" if not problems else "Ready (some indexers could not be added)"


async def run_forever() -> None:
    delay = 10
    while True:
        try:
            await run_once()
            log.info("setup complete")
            return
        except Exception as e:  # keep retrying; show the reason in the UI
            log.exception("setup failed")
            status.state, status.message = "error", f"Setup problem, retrying: {e}"
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
