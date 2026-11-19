"""Mediabox web app: search page + small JSON API over Radarr/Sonarr.

Runs on 127.0.0.1:5000 behind Caddy, which already did the login.
"""
import asyncio
import logging
import os
import shutil
import urllib.parse
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import provision, settings
from .arr import ArrError, poster, radarr, sonarr

logging.basicConfig(level=logging.INFO, format="[mediabox] %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(provision.run_forever())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def upstream(e: ArrError):
    return HTTPException(502, str(e))


def dav_path(path: str | None) -> str | None:
    """/data/media/movies/X (2001)/x.mkv -> /dav/movies/X%20%282001%29/x.mkv"""
    if not path:
        return None
    rel = os.path.relpath(path, settings.MEDIA_ROOT)
    if rel.startswith(".."):
        return None
    return "/dav/" + urllib.parse.quote(rel.replace(os.sep, "/"))


def queue_entry(q: dict, kind: str) -> dict:
    size = q.get("size") or 0
    left = q.get("sizeleft") or 0
    title = q.get("title")
    if kind == "movie" and q.get("movie"):
        title = f'{q["movie"].get("title")} ({q["movie"].get("year")})'
    elif kind == "tv" and q.get("series"):
        ep = q.get("episode") or {}
        title = q["series"].get("title")
        if ep:
            title += f' S{ep.get("seasonNumber", 0):02d}E{ep.get("episodeNumber", 0):02d}'
    return {
        "kind": kind,
        "title": title,
        "progress": round(100 * (1 - left / size), 1) if size else 0,
        "status": q.get("trackedDownloadState") or q.get("status"),
        "timeleft": q.get("timeleft"),
        "size": size,
        "errors": [m for s in q.get("statusMessages") or [] for m in s.get("messages", [])][:3],
    }


# ---------- pages ----------

@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


# ---------- overview ----------

@app.get("/api/overview")
async def overview():
    out = {"setup": provision.status.as_dict(), "disk": None, "downloads": [], "library": [], "errors": []}
    try:
        du = shutil.disk_usage(settings.DATA_ROOT)
        out["disk"] = {"total": du.total, "used": du.used, "free": du.free}
    except OSError:
        pass

    async def safe(coro, label):
        try:
            return await coro
        except ArrError as e:
            out["errors"].append(f"{label}: {e}")
            return []

    rq, sq, movies, series = await asyncio.gather(
        safe(radarr.get("/queue/details", includeMovie="true"), "Radarr queue"),
        safe(sonarr.get("/queue/details", includeSeries="true", includeEpisode="true"), "Sonarr queue"),
        safe(radarr.get("/movie"), "Radarr library"),
        safe(sonarr.get("/series"), "Sonarr library"),
    )
    out["downloads"] = [queue_entry(q, "movie") for q in rq] + [queue_entry(q, "tv") for q in sq]

    downloading_movies = {q.get("movieId") for q in rq}
    downloading_series = {q.get("seriesId") for q in sq}
    lib = []
    for m in movies:
        state = "ready" if m.get("hasFile") else ("downloading" if m["id"] in downloading_movies else "searching")
        lib.append({"type": "movie", "id": m.get("tmdbId"), "title": m.get("title"), "year": m.get("year"),
                    "poster": poster(m), "state": state, "added": m.get("added") or ""})
    for s in series:
        stats = s.get("statistics") or {}
        have = stats.get("episodeFileCount", 0)
        if s["id"] in downloading_series:
            state = "downloading"
        elif have:
            state = "ready"
        else:
            state = "searching"
        lib.append({"type": "tv", "id": s.get("tvdbId"), "title": s.get("title"), "year": s.get("year"),
                    "poster": poster(s), "state": state, "added": s.get("added") or "",
                    "episodes": f'{have}/{stats.get("episodeCount", 0)}'})
    lib.sort(key=lambda x: x["added"], reverse=True)
    out["library"] = lib[:24]
    return out


# ---------- search ----------

@app.get("/api/search")
async def search(q: str):
    q = q.strip()
    if not q:
        return []
    movies, shows = await asyncio.gather(
        radarr.get("/movie/lookup", term=q), sonarr.get("/series/lookup", term=q), return_exceptions=True
    )
    if isinstance(movies, Exception) and isinstance(shows, Exception):
        raise HTTPException(502, "Search is unavailable (apps still starting?)")
    results = []
    if isinstance(movies, list):
        for m in movies[:12]:
            if m.get("tmdbId"):
                results.append({"type": "movie", "id": m["tmdbId"], "title": m.get("title"), "year": m.get("year"),
                                "overview": (m.get("overview") or "")[:280], "poster": poster(m),
                                "inLibrary": bool(m.get("id"))})
    if isinstance(shows, list):
        for s in shows[:12]:
            if s.get("tvdbId"):
                results.append({"type": "tv", "id": s["tvdbId"], "title": s.get("title"), "year": s.get("year"),
                                "overview": (s.get("overview") or "")[:280], "poster": poster(s),
                                "inLibrary": bool(s.get("id"))})
    # Interleave so the best movie and best show both appear near the top
    mv = [r for r in results if r["type"] == "movie"]
    tv = [r for r in results if r["type"] == "tv"]
    mixed = []
    for i in range(max(len(mv), len(tv))):
        mixed += mv[i:i + 1] + tv[i:i + 1]
    return mixed


# ---------- add ----------

class AddRequest(BaseModel):
    type: Literal["movie", "tv"]
    id: int


async def pick_profile(arr) -> int:
    profiles = await arr.get("/qualityprofile")
    if not profiles:
        raise ArrError(f"{arr.name} has no quality profiles")
    for p in profiles:
        if p.get("name", "").lower() == settings.QUALITY_PROFILE.lower():
            return p["id"]
    return profiles[0]["id"]


@app.post("/api/add")
async def add(req: AddRequest):
    try:
        if req.type == "movie":
            existing = await radarr.get("/movie", tmdbId=req.id)
            if existing:
                m = existing[0]
                if not m.get("hasFile"):
                    await radarr.post("/command", {"name": "MoviesSearch", "movieIds": [m["id"]]})
                return {"ok": True, "already": True}
            movie = await radarr.get("/movie/lookup/tmdb", tmdbId=req.id)
            movie.update(
                qualityProfileId=await pick_profile(radarr),
                rootFolderPath=settings.MOVIES_ROOT,
                monitored=True,
                minimumAvailability="released",
                addOptions={"searchForMovie": True},
            )
            await radarr.post("/movie", movie)
            return {"ok": True, "already": False}

        existing = await sonarr.get("/series", tvdbId=req.id)
        if existing:
            await sonarr.post("/command", {"name": "SeriesSearch", "seriesId": existing[0]["id"]})
            return {"ok": True, "already": True}
        found = await sonarr.get("/series/lookup", term=f"tvdb:{req.id}")
        if not found:
            raise HTTPException(404, "Show not found")
        show = found[0]
        show.update(
            qualityProfileId=await pick_profile(sonarr),
            rootFolderPath=settings.TV_ROOT,
            monitored=True,
            seasonFolder=True,
            addOptions={"monitor": "all", "searchForMissingEpisodes": True},
        )
        await sonarr.post("/series", show)
        return {"ok": True, "already": False}
    except ArrError as e:
        raise upstream(e)


# ---------- item detail ----------

@app.get("/api/item/{kind}/{item_id}")
async def item(kind: Literal["movie", "tv"], item_id: int):
    try:
        if kind == "movie":
            found = await radarr.get("/movie", tmdbId=item_id)
            if not found:
                raise HTTPException(404, "Not in library")
            m = found[0]
            files = []
            mf = m.get("movieFile")
            if m.get("hasFile") and mf:
                path = mf.get("path") or os.path.join(m.get("path", ""), mf.get("relativePath", ""))
                files.append({"name": os.path.basename(path), "url": dav_path(path), "size": mf.get("size")})
            queue = await radarr.get("/queue/details", movieId=m["id"], includeMovie="true")
            state = "ready" if files else ("downloading" if queue else "searching")
            return {"type": "movie", "title": m.get("title"), "year": m.get("year"), "poster": poster(m),
                    "overview": m.get("overview"), "state": state, "files": files,
                    "downloads": [queue_entry(q, "movie") for q in queue]}

        found = await sonarr.get("/series", tvdbId=item_id)
        if not found:
            raise HTTPException(404, "Not in library")
        s = found[0]
        efiles = await sonarr.get("/episodefile", seriesId=s["id"])
        files = sorted(
            ({"name": f.get("relativePath") or os.path.basename(f.get("path", "")),
              "url": dav_path(f.get("path") or os.path.join(s.get("path", ""), f.get("relativePath", ""))),
              "size": f.get("size")} for f in efiles),
            key=lambda f: f["name"],
        )
        queue = await sonarr.get("/queue/details", seriesId=s["id"], includeSeries="true", includeEpisode="true")
        state = "downloading" if queue else ("ready" if files else "searching")
        return {"type": "tv", "title": s.get("title"), "year": s.get("year"), "poster": poster(s),
                "overview": s.get("overview"), "state": state, "files": files,
                "downloads": [queue_entry(q, "tv") for q in queue]}
    except ArrError as e:
        raise upstream(e)
