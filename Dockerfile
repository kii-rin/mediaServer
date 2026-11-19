# Mediabox: Radarr + Sonarr + Prowlarr + qBittorrent + FlareSolverr + WebDAV
# behind one login, auto-configured on first boot.
#
#   docker build -t server https://github.com/YOU/REPO.git
#   docker run -d --restart always -p 80:80 -v /data:/data \
#     -e USERNAME=bob -e PASSWORD=pass124 server
#
# Every version below is pinned. Nothing updates itself; to upgrade, change a
# version here, rebuild, test, and publish.

FROM python:3.11-slim-bookworm

ARG RADARR_VERSION=7.0.3.10912
ARG SONARR_VERSION=4.0.20.3012
ARG PROWLARR_VERSION=3.0.2.5777
ARG FLARESOLVERR_VERSION=3.5.2
ARG CADDY_VERSION=2.11.9
ARG S6_OVERLAY_VERSION=3.2.3.2

ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_VERBOSITY=1 \
    TZ=Etc/UTC

# System packages:
#  - arr apps need ICU + SQLite + MediaInfo
#  - qBittorrent and rclone (WebDAV) come from Debian, pinned to bookworm
#  - Chromium + Xvfb for FlareSolverr (same set its official image uses)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils tzdata \
      libicu72 libsqlite3-0 libmediainfo0v5 \
      qbittorrent-nox rclone \
      chromium chromium-common chromium-driver xvfb xauth dumb-init procps \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --user-group --home-dir /data/config --no-create-home --shell /usr/sbin/nologin media

# s6-overlay (process supervisor)
RUN curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" | tar -C / -Jxp \
 && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz" | tar -C / -Jxp

# Caddy (reverse proxy + login)
RUN curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_amd64.tar.gz" \
      | tar -C /usr/local/bin -xz caddy \
 && caddy version

# Radarr, Sonarr, Prowlarr (official self-contained builds)
RUN mkdir -p /opt/radarr /opt/sonarr /opt/prowlarr \
 && curl -fsSL "https://github.com/Radarr/Radarr/releases/download/v${RADARR_VERSION}/Radarr.master.${RADARR_VERSION}.linux-core-x64.tar.gz" \
      | tar -C /opt/radarr -xz --strip-components=1 \
 && curl -fsSL "https://github.com/Sonarr/Sonarr/releases/download/v${SONARR_VERSION}/Sonarr.main.${SONARR_VERSION}.linux-x64.tar.gz" \
      | tar -C /opt/sonarr -xz --strip-components=1 \
 && curl -fsSL "https://github.com/Prowlarr/Prowlarr/releases/download/v${PROWLARR_VERSION}/Prowlarr.master.${PROWLARR_VERSION}.linux-core-x64.tar.gz" \
      | tar -C /opt/prowlarr -xz --strip-components=1 \
 && rm -rf /opt/radarr/Radarr.Update /opt/sonarr/Sonarr.Update /opt/prowlarr/Prowlarr.Update \
 && test -x /opt/radarr/Radarr && test -x /opt/sonarr/Sonarr && test -x /opt/prowlarr/Prowlarr

# FlareSolverr (from source, laid out exactly like its official image:
# code in /app, chromedriver at /app/chromedriver, package.json one level up)
RUN mkdir -p /app \
 && curl -fsSL "https://github.com/FlareSolverr/FlareSolverr/archive/refs/tags/v${FLARESOLVERR_VERSION}.tar.gz" \
      | tar -C /tmp -xz \
 && cp -r /tmp/FlareSolverr-${FLARESOLVERR_VERSION}/src/. /app/ \
 && cp /tmp/FlareSolverr-${FLARESOLVERR_VERSION}/package.json / \
 && pip install --no-cache-dir -r /tmp/FlareSolverr-${FLARESOLVERR_VERSION}/requirements.txt \
 && mv /usr/bin/chromedriver /app/chromedriver \
 && chown -R media:media /app \
 && rm -rf /tmp/FlareSolverr-*

# Our app (search page + auto-setup), in its own venv so its packages
# never clash with FlareSolverr's pinned ones
COPY app/requirements.txt /opt/mediabox/requirements.txt
RUN python -m venv /opt/mediabox/venv \
 && /opt/mediabox/venv/bin/pip install --no-cache-dir -r /opt/mediabox/requirements.txt
COPY app/ /opt/mediabox/app/
COPY indexers.yml /opt/mediabox/indexers.yml
COPY rootfs/ /

RUN chmod +x /etc/s6-overlay/scripts/* /etc/s6-overlay/s6-rc.d/*/run \
 && /opt/mediabox/venv/bin/python -c "import sys; sys.path.insert(0,'/opt/mediabox'); import app.main"

EXPOSE 80
VOLUME /data
ENTRYPOINT ["/init"]
