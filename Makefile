# INTERCEPT — Makefile
# Common operations and environment setup.

VENV        := venv/bin/python
PIP         := venv/bin/pip
DB_USER     := intercept
DB_PASS     := intercept
DB_NAME     := intercept_adsb
DB_HOST     := localhost
PSQL        := PGPASSWORD=$(DB_PASS) psql -U $(DB_USER) -d $(DB_NAME) -h $(DB_HOST)

.PHONY: help start start-debug stop restart env-setup db-check adsb-status adsb-start test lint

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  start          Start production server (gunicorn + gevent)"
	@echo "  start-debug    Start Flask dev server (debug mode)"
	@echo "  stop           Stop all intercept processes"
	@echo "  restart        stop + start"
	@echo "  env-setup      Write .env with ADSB history enabled"
	@echo "  db-check       Show ADS-B message counts in PostgreSQL"
	@echo "  adsb-status    Show live ADS-B session and recent messages"
	@echo "  adsb-start     Start ADS-B via API (app must be running)"
	@echo "  test           Run pytest suite"
	@echo "  lint           Run ruff linter"

# ── App lifecycle ────────────────────────────────────────────────────────────

start:
	sudo ./start.sh

start-debug:
	sudo $(VENV) intercept.py --debug

stop:
	@sudo pkill -f "intercept.py" 2>/dev/null || true
	@sudo pkill -f "gunicorn.*app:app" 2>/dev/null || true
	@echo "Stopped."

restart: stop
	@sleep 2
	$(MAKE) start

# ── Environment setup ────────────────────────────────────────────────────────
#
# Fix: app.py had load_dotenv=False and only start.sh sourced .env via bash,
# so running intercept.py directly left INTERCEPT_ADSB_HISTORY_ENABLED unset
# (defaulting to False). Fixed in two places:
#   1. intercept.py — loads .env via python-dotenv before importing app
#   2. app.py       — loads .env before "from config import ...", covering
#                     gunicorn (which imports app.py directly, bypassing
#                     intercept.py)
# override=False so explicit env vars always win over .env values.

env-setup:
	@test -f .env || touch .env
	@grep -q INTERCEPT_ADSB_HISTORY_ENABLED .env || \
		echo "INTERCEPT_ADSB_HISTORY_ENABLED=true" >> .env
	@grep -q INTERCEPT_ADSB_AUTO_START .env || \
		echo "INTERCEPT_ADSB_AUTO_START=true" >> .env
	@echo ".env updated:"
	@cat .env

# ── Database ─────────────────────────────────────────────────────────────────

db-check:
	@$(PSQL) -c "\
	  SELECT \
	    count(*)             AS total_messages, \
	    count(DISTINCT icao) AS unique_aircraft, \
	    max(received_at)     AS latest_message \
	  FROM adsb_messages;"

adsb-status:
	@echo "--- Active sessions ---"
	@$(PSQL) -c "\
	  SELECT id, started_at, sdr_type, start_source \
	  FROM adsb_sessions \
	  WHERE ended_at IS NULL \
	  ORDER BY started_at DESC LIMIT 5;"
	@echo "--- Last 5 messages ---"
	@$(PSQL) -c "\
	  SELECT icao, callsign, altitude, lat, lon, received_at \
	  FROM adsb_messages \
	  ORDER BY received_at DESC LIMIT 5;"

# ── API helpers ──────────────────────────────────────────────────────────────

adsb-start:
	@CSRF=$$(curl -sc /tmp/ic_mk.txt http://localhost:5050/login \
	  | grep -oP 'name="csrf_token" value="\K[^"]+'); \
	curl -sc /tmp/ic_mk.txt -b /tmp/ic_mk.txt \
	  -X POST http://localhost:5050/login \
	  -H "Content-Type: application/x-www-form-urlencoded" \
	  --data-urlencode "username=admin" \
	  --data-urlencode "password=admin" \
	  --data-urlencode "csrf_token=$$CSRF" -o /dev/null; \
	curl -s -b /tmp/ic_mk.txt \
	  -X POST http://localhost:5050/adsb/start \
	  -H "Content-Type: application/json" \
	  -d '{"gain":"40","device":"0"}' | python3 -m json.tool

# ── Development ──────────────────────────────────────────────────────────────

test:
	$(VENV) -m pytest

lint:
	venv/bin/ruff check .
