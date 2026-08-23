# INTERCEPT on Unraid

Install INTERCEPT as a Docker app, then finish setup in the browser wizard.

## Community Applications (app)

1. Install the **Community Applications** plugin if it is not already present.
2. Add this template repository (or load the XML by hand):

   `https://raw.githubusercontent.com/smittix/intercept/main/unraid/intercept.xml`

3. In **Docker → Add Container**, switch to Advanced View and load `unraid/intercept.xml`, or search for **INTERCEPT** once the template is published.
4. Set a real **Admin password**. Leave privileged mode and `/dev/bus/usb` enabled so SDR dongles work.
5. Prefer a **cache/pool** path for the application database:

   `/mnt/cache/appdata/intercept/instance`

   Unraid `/mnt/user/...` paths are FUSE (shfs). INTERCEPT detects that and falls back from SQLite WAL to DELETE journal mode, which is safe but slower.
6. Apply, wait for the health check, then open `http://TOWER:5050/`.
7. Sign in and complete **Quick Setup**: USB/SDR check, observer location, default mode, display mode.

The published image is `ghcr.io/smittix/intercept:latest`. If that tag is not available yet, build locally on the Unraid box:

```bash
git clone https://github.com/smittix/intercept.git /mnt/user/appdata/intercept/src
cd /mnt/user/appdata/intercept/src
docker build -t ghcr.io/smittix/intercept:latest .
```

Then start the template against the local tag.

## Persistent layout

| Host path | Container | Contents |
|-----------|-----------|----------|
| `/mnt/cache/appdata/intercept/instance` | `/app/instance` | SQLite (`intercept.db`), TLE cache |
| `/mnt/user/appdata/intercept/data` | `/app/data` | Weather-sat, radiosonde, Sub-GHz, ADS-B files |
| `/mnt/user/appdata/intercept/config` | `/config` | Wizard-written `.env` |

Back up `instance/` and `config/`. Capture files in `data/` can be large.

## USB SDR notes

- Privileged mode is required so `rtl_test`, `hackrf_info`, and SoapySDR can open the dongle.
- Bind `/dev/bus/usb`. After plugging in a new dongle, restart the container.
- If the kernel claims an RTL-SDR as a DVB tuner, blacklist those modules on the Unraid host (see `docs/HARDWARE.md`).
- WiFi monitor-mode scanning needs host networking and is not the default. Use a dedicated Linux install or host-network compose for that mode.

## Health

- Liveness: `GET /health`
- Wizard status (after login): `GET /setup/status`

Do not publish port 5050 to the public Internet. Keep the WebUI on the LAN or behind a VPN.
