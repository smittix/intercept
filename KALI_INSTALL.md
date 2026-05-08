# Kali Linux Install (Live or Installed)

Quick-start guide for running INTERCEPT on Kali with ADS-B history enabled.
No PostgreSQL required — history is stored in SQLite automatically.

## 1. Install system dependencies

```bash
sudo apt update && sudo apt install -y \
    python3 python3-pip python3-venv \
    rtl-sdr dump1090-mutability \
    git
```

## 2. Clone and switch to branch

```bash
git clone https://github.com/UnderOverInput/intercept.git
cd intercept
git checkout claude/fix-adsb-history-kali-lTIuy
```

If you already have the repo cloned:

```bash
cd intercept
git fetch origin
git checkout claude/fix-adsb-history-kali-lTIuy
git pull origin claude/fix-adsb-history-kali-lTIuy
```

## 3. Set up Python environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install flask flask-wtf flask-compress flask-limiter requests Werkzeug
```

## 4. Start with ADS-B history enabled

```bash
sudo INTERCEPT_ADSB_HISTORY_ENABLED=true ./start.sh
```

INTERCEPT is then available at: **http://localhost:5050**

ADS-B history is stored at `instance/adsb_history.db` (SQLite, created automatically).

---

## Optional: persist the setting so you don't have to type it every time

```bash
cp .env.example .env
echo "INTERCEPT_ADSB_HISTORY_ENABLED=true" >> .env
```

Then just run:

```bash
sudo ./start.sh
```

---

## Optional: full install (all modules — satellites, Bluetooth, weather, etc.)

```bash
pip install -r requirements.txt
```

---

## Notes

- `psycopg2` and PostgreSQL are **not required** for ADS-B history on Kali.
  The SQLite fallback is automatic when `INTERCEPT_ADSB_HISTORY_ENABLED=true`.
- Set `INTERCEPT_ADSB_DB_BACKEND=postgres` to force PostgreSQL if you have it running.
- SQLite database path can be overridden: `INTERCEPT_ADSB_SQLITE_PATH=/path/to/file.db`
