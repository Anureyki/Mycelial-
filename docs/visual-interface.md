# Visual interface: Anansi web UI + Jellyfin media library

Set up 2026-10-18. Everything below was verified on this server
(Ubuntu 26.04.1, 4 cores, 7.1 GB RAM, Intel 4th-gen iGPU, LAN 192.168.1.139).

## Where things are

| What | URL / path | Access |
|---|---|---|
| Anansi web UI (existing PWA, reused) | https://192.168.1.139:8443/ | TLS + basic auth (existing `state/nginx/htpasswd`) |
| Jellyfin, on the LAN | https://192.168.1.139:8920/ | TLS (Mycelial certificate, self-signed) + Jellyfin accounts |
| Jellyfin, on the server itself | http://127.0.0.1:8096/ | loopback only - refused from the LAN |
| Media library | `~/mycelial/media/anansi/{images,reports,diagrams,audio,video,thumbnails}` | 700 dirs, 600 files; mounted read-only into Jellyfin; gitignored |
| Jellyfin config/cache | `~/.local/share/mycelial/jellyfin/{config,cache}` | 700 - outside the repo's `state/` because Jellyfin writes 755 dirs |
| Jellyfin accounts | `~/.local/share/mycelial/jellyfin/credentials.env` | 600. `mycelial-admin` (admin), `tv` (viewer, not admin). NOT loaded by `start_all.sh`, so no agent inherits them |
| Anansi's Jellyfin key | `.env`: `JELLYFIN_URL`, `JELLYFIN_API_KEY` | granted to `anansi` in `config/resource_acl.json` |

Libraries: **Anansi Images** (images, reports, diagrams), **Anansi Video**, **Anansi Audio**.
Remote access is disabled inside Jellyfin; nothing is port-forwarded.

## Services

- **Jellyfin**: Docker container `jellyfin`, image `jellyfin/jellyfin:10.10.7` (pinned), run as
  uid 1000, `--memory 1500m --cpus 2`, `--restart unless-stopped`. Docker is enabled at boot.
- **TLS proxy**: the unprivileged Mycelial nginx (`config/nginx/mycelial.conf`, started by
  `start_all.sh`, which `mycelial.service` runs at boot). Port 8920 was added; backup of the
  previous config in `state/backups/mycelial.conf.2026-10-18.bak`.
- Reload after editing the proxy config:
  `nginx -t -c ~/mycelial/config/nginx/mycelial.conf -p ~/mycelial && nginx -s reload -c ~/mycelial/config/nginx/mycelial.conf -p ~/mycelial`

## Publishing media (Anansi)

```bash
curl -X POST localhost:8081/execute -H 'Content-Type: application/json' -d '{"task":"publish_media",
  "args":{"source":"state/artifacts/<file>.png","kind":"report","title":"...","description":"..."}}'
```

`kind`: image, report, diagram (png/jpg/webp, 25 MB), video (mp4/webm/mkv, 300 MB),
audio (mp3/ogg/flac/wav/m4a, 100 MB). Sources must sit in `state/artifacts/` or `reports/`
(resolved paths - `..` and symlinks out are refused); the file's bytes must match its type;
title/description are scanned for personal identifiers; the file is copied (never moved) as
600, with a `.json` metadata sidecar, then Jellyfin is asked to rescan. Every call is logged
to the audit service as `MEDIA_PUBLISH`. Gate: `tools/check_media_publish.py`.

Demo report: `python3 tools/grow_report_card.py current_plant` renders a plant's recorded
readings (measured values and Grow's assessment in separate panels) into `state/artifacts/`.

## Not done - needs you (sudo)

**TV kiosk.** The HDMI output is a text console: no display server and no browser are
installed, and installing them needs root. Because the TV is the server's own screen, a
kiosk can use the loopback address with no certificate warning. One way (untested here):

```bash
sudo apt install cage firefox          # cage = single-app Wayland kiosk
# then run on tty1 only, keeping tty2 (Ctrl+Alt+F2) as the admin console:
cage -- firefox --kiosk http://127.0.0.1:8096
```

Sign the TV in as **tv**, never as the admin. Autostart on tty1 replaces the console on
that terminal - decide that before enabling it.

## Limits

- Disk is at 91% (5.0 GB free after the 1.76 GB image). Video fills it quickly.
- Reports are published as images; PDFs are not supported by this workflow.
- The LAN certificate is self-signed: browsers and Jellyfin apps on other devices will warn
  until `state/certs/mycelial.crt` is trusted on them.
