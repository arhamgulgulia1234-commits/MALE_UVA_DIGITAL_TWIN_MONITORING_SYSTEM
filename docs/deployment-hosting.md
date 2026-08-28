# Connecting the Vercel frontend to the Render backend

Two services, three settings. Everything below assumes:

- **Backend on Render** at `https://<your-service>.onrender.com`
- **Frontend on Vercel** at `https://<your-app>.vercel.app`

Substitute your real hostnames.

---

## 1. Render — set the allowed origin

Dashboard → your service → **Environment** → add:

| Key | Value |
|---|---|
| `CORS_ORIGINS` | `https://<your-app>.vercel.app` |
| `SIM_TICK_HZ` | `10` |
| `USE_MOCK` | `false` |

**No trailing slash.** `https://foo.vercel.app/` will not match the browser's `Origin`
header, which is sent without one.

If you want preview deployments to work too, add them comma-separated — Vercel gives every
branch its own hostname, and each is a distinct origin:

```
https://<your-app>.vercel.app,https://<your-app>-git-main-<team>.vercel.app
```

Save and let it redeploy.

## 2. Vercel — point the frontend at the backend

Project → **Settings → Environment Variables** → add for Production (and Preview if you
use it):

| Key | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://<your-service>.onrender.com` |
| `NEXT_PUBLIC_WS_URL` | `wss://<your-service>.onrender.com/ws/telemetry` |

Two things that catch people here:

- **`wss://`, not `ws://`.** Vercel serves your page over HTTPS, and a browser blocks an
  insecure WebSocket opened from a secure page. It fails silently and looks exactly like
  the backend being down. The client now force-upgrades `ws://` → `wss://` on an HTTPS
  page as a safety net, but set it correctly anyway.
- **`NEXT_PUBLIC_*` values are baked in at build time**, not read at runtime. Setting them
  is not enough — you must **redeploy** afterwards. Deployments → ⋯ → Redeploy.

## 3. Verify

```bash
# Backend alive?
curl https://<your-service>.onrender.com/health
# -> {"status":"ok"}

# CORS actually allowing your Vercel origin?
curl -s -D- -o /dev/null \
  -H "Origin: https://<your-app>.vercel.app" \
  https://<your-service>.onrender.com/health | grep -i access-control-allow-origin
# -> access-control-allow-origin: https://<your-app>.vercel.app
```

If that second command prints nothing, `CORS_ORIGINS` does not match — check for a
trailing slash, `http` vs `https`, or a stale deploy.

Then open the Vercel URL. The Mission Header status pill should read **Live**. If it sits
on *Connecting* or *Reconnecting*, open DevTools → Network → WS and read the failure:

| Symptom | Cause |
|---|---|
| `Mixed Content` blocked | `NEXT_PUBLIC_WS_URL` is `ws://` — change to `wss://` and redeploy |
| Connects then closes immediately, code 1008 | Auth is on but the token is missing/wrong |
| CORS error on `/control/*` but WS works | `CORS_ORIGINS` wrong (WebSockets are not subject to CORS, which is why one can work while the other fails) |
| Long stall, then connects | Render free tier cold start — see below |

---

## Render free tier: what to expect

Three consequences worth knowing before a live demo, none of them bugs:

**It spins down after ~15 minutes of inactivity.** The first request afterwards takes
roughly 50 seconds to wake the container. The dashboard will sit on *Reconnecting* for
that whole time — the reconnecting client handles it, but it looks broken to an audience.
**Hit the URL a minute before you present**, or put a cron on `/health` every 10 minutes.

**The simulation only runs while the service is awake.** The mission clock and any
recording stop when it sleeps and resume from wherever they were. Not a problem for a
demo, but do not read the mission timeline as continuous across a sleep.

**The disk is ephemeral.** `backend/data/telemetry.db` is wiped on every restart and every
deploy, so recorded missions and their reports do not survive. Replay works fine within a
session. To keep missions, attach a Render persistent disk mounted at `/app/data` (paid),
or point `DATABASE_URL` at a managed Postgres — the repository layer is plain SQLAlchemy
and the only SQLite-specific code is the PRAGMA block in `app/db/session.py`.

## Optional: turn on auth

On Render:

```
TELEMETRY_AUTH_ENABLED=true
TELEMETRY_TOKEN=<a long random string>
```

On Vercel add `NEXT_PUBLIC_TELEMETRY_TOKEN` with the same value, then redeploy.

Be clear-eyed about what this is: a shared secret compiled into a public JavaScript
bundle. Anyone who opens DevTools can read it. It keeps casual traffic off a public demo
and nothing more — `docs/deployment-roadmap.md` sets out what real authentication needs.

## Cost note

The physics loop runs continuously at 10 Hz whether or not anyone is watching, so the
backend burns CPU the entire time it is awake. On Render's free tier that is what it is;
on a paid instance it is worth knowing you are paying for a simulation with no viewers.
Gating the tick loop on `ws_telemetry.manager.client_count > 0` would fix that, at the
cost of the mission clock not advancing while nobody is connected.
