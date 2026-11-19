# Mediabox

Type a movie or show name, tap **Get**, and stream it when it's ready.
One container. One command. Everything already set up.

## Run it

You need Docker on a Linux server (a DigitalOcean droplet works fine).

```sh
docker build -t server https://github.com/YOU/REPO.git
docker run -d --restart always -p 80:80 -v /data:/data -e USERNAME=bob -e PASSWORD=pass124 server
```

Change `bob` and `pass124` to your own login. That's it.

Open `http://YOUR-SERVER-IP` and log in.

| Address | What it is |
|---|---|
| `http://IP/` | Search page: search, request, see progress, play |
| `http://IP/radarr` | Radarr (movies) |
| `http://IP/sonarr` | Sonarr (TV) |
| `http://IP/prowlarr` | Prowlarr (indexers) |
| `http://IP/qbit` | qBittorrent (downloads) |
| `http://IP/dav` | WebDAV, for streaming in VLC, Infuse, Kodi… |

The same username and password work everywhere, including in VLC and Infuse.

## Watching

Tap a title in your library and then:

- **Play** opens it in the browser (works for most MP4 files)
- **Open in VLC** hands it to the VLC app on your phone or computer
- **Copy link** gives you a link for any player that can stream from a URL

Or add a WebDAV server in your player: address `http://IP/dav`, plus your username and password.

## Where things are on the server

```
/data/
  media/movies/   finished movies
  media/tv/       finished shows
  downloads/      downloads in progress
  config/         app settings, keys, databases
  config/indexers.yml   which sources to search
```

Deleting and re-running the container keeps everything, because it all lives in `/data`.

## Indexers (where it searches)

The defaults are legal sources only (Internet Archive). To add more, edit
`/data/config/indexers.yml` and restart the container:

```sh
docker restart $(docker ps -q --filter ancestor=server)
```

```yaml
indexers:
  - definition: internetarchive   # Prowlarr's id for the indexer
    name: Internet Archive
    flaresolverr: false           # true if the site uses Cloudflare protection
```

The ids are the file names in https://github.com/Prowlarr/Indexers.
You can also add indexers by hand at `http://IP/prowlarr`.

Only download content you have the right to.

## What happens on first start

1. The container checks that `USERNAME` and `PASSWORD` are set. If either is missing, it refuses to start.
2. It creates the folders under `/data`.
3. It generates random API keys and writes each app's settings: its URL path, no self-updates, and login handled by Caddy.
4. It starts every service under s6.
5. The app connects everything: qBittorrent to Radarr and Sonarr, the root folders, Prowlarr to Radarr and Sonarr, FlareSolverr, and the indexers.

Step 5 runs on every start and only adds what's missing, so it also repairs anything deleted by mistake.
Progress is shown at the top of the search page.

## Inside the image

| Part | Version | Job |
|---|---|---|
| Radarr | 7.0.3.10912 | Finds and organises movies |
| Sonarr | 4.0.20.3012 | Finds and organises TV |
| Prowlarr | 3.0.2.5777 | Manages indexers |
| qBittorrent | Debian bookworm (4.5.x) | Downloads |
| FlareSolverr | 3.5.2 | Gets past Cloudflare checks |
| rclone | Debian bookworm | WebDAV server |
| Caddy | 2.11.9 | One port, one login, routing |
| s6-overlay | 3.2.3.2 | Keeps every service running |

## Updates

Nothing updates itself. Versions are pinned in the `Dockerfile`. To upgrade:

1. Change the version numbers in the `Dockerfile`.
2. Build and test.
3. Push to GitHub. Users then run the `docker build` command again, remove the old container, and run the `docker run` command again. Their data in `/data` is kept.

## Good to know

- **Plain HTTP for now.** Logins are sent unencrypted. HTTPS is the next step.
- **Port 80 must be free** on the server.
- **x86-64 (amd64) only** for now.
- **Size:** about 1.5 GB image. Plan for at least 2 GB of RAM.
- **Disk:** small droplets fill up fast. Attach a DigitalOcean Volume and mount it at `/data`.
- **No incoming torrent port** is opened, so downloads work but may find fewer peers.

## Licenses

Radarr, Sonarr and Prowlarr are GPL-3.0; qBittorrent is GPL-2.0+; FlareSolverr is MIT;
rclone is MIT; Caddy is Apache-2.0. This repo downloads their official releases and
source during the build. Source for each is at its project's GitHub page.
