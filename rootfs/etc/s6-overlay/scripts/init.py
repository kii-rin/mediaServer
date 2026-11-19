#!/usr/local/bin/python3
"""First thing that runs in the container (before any app starts).

- refuses to start without USERNAME / PASSWORD
- creates the /data layout
- creates (once) and reuses API keys
- writes each app's config so it starts already set up
- writes the Caddy config with the login
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

DATA = os.environ.get("MB_DATA_ROOT", "/data")
CONFIG = f"{DATA}/config"
UID = GID = 1000

ARR_APPS = {
    # name: (port, url base)
    "radarr": (7878, "/radarr"),
    "sonarr": (8989, "/sonarr"),
    "prowlarr": (9696, "/prowlarr"),
}


def fail(msg: str) -> None:
    print("\n" + "=" * 60, file=sys.stderr)
    print(f"  MEDIABOX CAN'T START: {msg}", file=sys.stderr)
    print("  Example:", file=sys.stderr)
    print("    docker run -d --restart always -p 80:80 -v /data:/data \\", file=sys.stderr)
    print("      -e USERNAME=bob -e PASSWORD=pass124 server", file=sys.stderr)
    print("=" * 60 + "\n", file=sys.stderr)
    sys.exit(1)


def check_login() -> tuple[str, str]:
    user = os.environ.get("USERNAME", "").strip()
    pw = os.environ.get("PASSWORD", "")
    if not user or not pw:
        fail("USERNAME and PASSWORD are required.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", user):
        fail("USERNAME may only use letters, numbers, dot, dash and underscore.")
    return user, pw


def make_dirs() -> None:
    for d in [
        CONFIG,
        f"{CONFIG}/mediabox",
        f"{CONFIG}/radarr",
        f"{CONFIG}/sonarr",
        f"{CONFIG}/prowlarr",
        f"{CONFIG}/qbittorrent/qBittorrent/config",
        f"{CONFIG}/flaresolverr",
        f"{DATA}/downloads",
        f"{DATA}/media/movies",
        f"{DATA}/media/tv",
    ]:
        os.makedirs(d, exist_ok=True)


def load_keys() -> dict:
    path = f"{CONFIG}/mediabox/keys.json"
    keys = {}
    if os.path.exists(path):
        with open(path) as f:
            keys = json.load(f)
    for app in ARR_APPS:
        # If the app already has a config.xml (e.g. restored backup), trust its key.
        existing = read_xml_value(f"{CONFIG}/{app}/config.xml", "ApiKey")
        keys[app] = existing or keys.get(app) or secrets.token_hex(16)
    with open(path, "w") as f:
        json.dump(keys, f, indent=2)
    os.chmod(path, 0o600)
    return keys


def read_xml_value(path: str, tag: str) -> str | None:
    if not os.path.exists(path):
        return None
    try:
        el = ET.parse(path).getroot().find(tag)
        return el.text.strip() if el is not None and el.text else None
    except ET.ParseError:
        return None


def write_arr_config(app: str, port: int, url_base: str, api_key: str) -> None:
    """Create or patch config.xml. Only the keys we care about are forced;
    anything else the user changes in the app's UI is kept."""
    path = f"{CONFIG}/{app}/config.xml"
    root = None
    if os.path.exists(path):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            root = None
    if root is None:
        root = ET.Element("Config")

    forced = {
        "BindAddress": "127.0.0.1",       # only reachable through Caddy
        "Port": str(port),
        "UrlBase": url_base,
        "ApiKey": api_key,
        "AuthenticationMethod": "External",  # Caddy does the login
        "AuthenticationRequired": "Enabled",
        "UpdateMechanism": "Docker",      # never self-update
        "UpdateAutomatically": "False",
        "AnalyticsEnabled": "False",
        "LaunchBrowser": "False",
        "EnableSsl": "False",
        "InstanceName": app.capitalize(),
    }
    defaults = {"LogLevel": "info", "Branch": "master" if app != "sonarr" else "main"}

    for tag, value in forced.items():
        el = root.find(tag)
        if el is None:
            el = ET.SubElement(root, tag)
        el.text = value
    for tag, value in defaults.items():
        if root.find(tag) is None:
            ET.SubElement(root, tag).text = value

    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8")


def write_qbittorrent_config() -> None:
    path = f"{CONFIG}/qbittorrent/qBittorrent/config/qBittorrent.conf"
    if os.path.exists(path):
        # Keep the user's tweaks, but make sure the WebUI stays reachable.
        with open(path) as f:
            text = f.read()
        if "WebUI\\Port=8081" in text and "WebUI\\LocalHostAuth=false" in text:
            return
    with open(path, "w") as f:
        f.write(
            "[LegalNotice]\n"
            "Accepted=true\n\n"
            "[BitTorrent]\n"
            "Session\\DefaultSavePath=/data/downloads/\n"
            "Session\\TempPathEnabled=false\n"
            "Session\\Port=6881\n"
            "Session\\QueueingSystemEnabled=true\n"
            "Session\\GlobalMaxSeedingMinutes=1440\n\n"
            "[Preferences]\n"
            "Downloads\\SavePath=/data/downloads/\n"
            "General\\Locale=en\n"
            "WebUI\\Address=127.0.0.1\n"
            "WebUI\\Port=8081\n"
            "WebUI\\LocalHostAuth=false\n"
            "WebUI\\HostHeaderValidation=false\n"
            "WebUI\\CSRFProtection=false\n"
            "WebUI\\ClickjackingProtection=false\n"
            "WebUI\\UseUPnP=false\n"
        )


def write_caddyfile(user: str, password: str) -> None:
    hashed = subprocess.run(
        ["caddy", "hash-password", "--plaintext", password],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    caddyfile = f"""{{
	admin off
	auto_https off
	persist_config off
}}

:80 {{
	basic_auth {{
		{user} {hashed}
	}}

	handle /radarr* {{
		reverse_proxy 127.0.0.1:7878
	}}
	handle /sonarr* {{
		reverse_proxy 127.0.0.1:8989
	}}
	handle /prowlarr* {{
		reverse_proxy 127.0.0.1:9696
	}}

	redir /qbit /qbit/
	handle_path /qbit/* {{
		reverse_proxy 127.0.0.1:8081 {{
			header_up Host 127.0.0.1:8081
			header_up -Origin
			header_up -Referer
		}}
	}}

	handle /dav* {{
		reverse_proxy 127.0.0.1:8090 {{
			flush_interval -1
		}}
	}}

	handle {{
		reverse_proxy 127.0.0.1:5000
	}}
}}
"""
    path = os.environ.get("MB_CADDYFILE", "/etc/caddy/Caddyfile")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(caddyfile)
    os.chmod(path, 0o600)


def copy_indexers_file() -> None:
    dst = f"{CONFIG}/indexers.yml"
    if not os.path.exists(dst):
        shutil.copy(os.environ.get("MB_DEFAULT_INDEXERS", "/opt/mediabox/indexers.yml"), dst)


def fix_ownership() -> None:
    # Config is ours to own entirely; for media/downloads only fix the
    # top-level folders so a huge library doesn't slow every boot.
    for root, dirs, files in os.walk(CONFIG):
        for name in dirs + files:
            try:
                os.lchown(os.path.join(root, name), UID, GID)
            except OSError:
                pass
    for d in [DATA, CONFIG, f"{DATA}/downloads", f"{DATA}/media",
              f"{DATA}/media/movies", f"{DATA}/media/tv"]:
        os.chown(d, UID, GID)


def main() -> None:
    user, pw = check_login()
    make_dirs()
    keys = load_keys()
    for app, (port, base) in ARR_APPS.items():
        write_arr_config(app, port, base, keys[app])
    write_qbittorrent_config()
    write_caddyfile(user, pw)
    copy_indexers_file()
    fix_ownership()
    print(f"[mediabox] ready: log in as '{user}' on port 80")


if __name__ == "__main__":
    main()
