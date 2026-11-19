"""All paths/addresses in one place. Defaults match the container; the env
vars exist so the app can be tested against fake services."""
import json
import os

DATA_ROOT = os.environ.get("MB_DATA_ROOT", "/data")
MEDIA_ROOT = os.environ.get("MB_MEDIA_ROOT", f"{DATA_ROOT}/media")
MOVIES_ROOT = f"{MEDIA_ROOT}/movies"
TV_ROOT = f"{MEDIA_ROOT}/tv"
KEYS_FILE = os.environ.get("MB_KEYS_FILE", f"{DATA_ROOT}/config/mediabox/keys.json")
INDEXERS_FILE = os.environ.get("MB_INDEXERS_FILE", f"{DATA_ROOT}/config/indexers.yml")

RADARR_URL = os.environ.get("MB_RADARR_URL", "http://127.0.0.1:7878/radarr")
SONARR_URL = os.environ.get("MB_SONARR_URL", "http://127.0.0.1:8989/sonarr")
PROWLARR_URL = os.environ.get("MB_PROWLARR_URL", "http://127.0.0.1:9696/prowlarr")

# Addresses the apps use to reach each other (all inside the container)
QBIT_HOST = "127.0.0.1"
QBIT_PORT = 8081
FLARESOLVERR_URL = "http://127.0.0.1:8191/"

# Quality profile used for new requests. "Any" grabs whatever exists,
# which suits small/legal indexers best. Falls back to the first profile.
QUALITY_PROFILE = os.environ.get("QUALITY_PROFILE", "Any")


def api_keys() -> dict:
    with open(KEYS_FILE) as f:
        return json.load(f)
