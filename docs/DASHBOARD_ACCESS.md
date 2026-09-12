# Reaching the dashboard

`https://192.168.1.139:8443/` — user `grower`.

## It was never the dashboard

Every layer was healthy the whole time it looked broken:

| Layer | State |
|-------|-------|
| mycelial nginx (pid owned by `anureyki`, `-c config/nginx/mycelial.conf`) | running |
| TLS certificate | valid to 2028-11-26, SAN includes `IP:192.168.1.139` |
| `WWW-Authenticate: Basic realm="Mycelial"` | present |
| `/execute` proxy → `127.0.0.1:8081` | Anansi answering 200 |
| service worker | network-first, returns a real 503 on a miss |

**The server had no IPv4 address.** The phone connects to `192.168.1.139`, an
IPv4 address, and the box came up IPv6-only because the router answered DHCPv6
and stayed silent on DHCPv4. With no route there is no connection — which is
exactly why there was no certificate warning and no login prompt. Nothing to
warn about.

The same fault blocked `git push`, because github.com publishes no AAAA record.
One cause, two symptoms that looked unrelated.

## Two nginx instances, which is not a fault

`nginx -T` shows only the **system** nginx — port 80, WordPress. The dashboard
runs as a **second, separate instance** started with
`nginx -c config/nginx/mycelial.conf -p /home/anureyki/mycelial`, owned by the
user rather than root, with its own pid file and logs under `state/` and
`logs/`.

Looking only at `/etc/nginx` makes the 8443 vhost appear to be a ghost config
running from deleted files. It is not. Check `ps -o args -p $(cat
state/nginx/nginx.pid)` before concluding anything about it.

## What to expect on the phone

1. **A certificate warning.** The cert is self-signed. Accept it once, or trust
   it in Settings — a service worker fetch *cannot* show the certificate
   prompt, so an untrusted cert inside the installed app fails silently.
2. **A login prompt.** Basic auth, user `grower`.
3. The app.

If the shell looks stale after an update, the version markers in `index.html`
and `service-worker.js` must match — `tools/check_shell_version.py` is a build
gate for exactly that.

## The network fix, and what is still owed

`netplan` now carries a static `192.168.1.139/24` as a **fallback**, with
`dhcp4: true` kept so a working lease is still preferred. `netplan apply`
prompted a fresh negotiation and the router answered with the same address, so
both routes now exist via the same gateway.

**The real fix is a DHCP reservation for `b8:8a:60:1c:52:fc` on the router.**
The static entry is a floor under a router that stopped answering once; it is
not a substitute for one that answers.
