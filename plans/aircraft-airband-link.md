# Link an ADS-B aircraft to airband ATC audio (issue #271, part 2)

Status: **proposed, awaiting approval.** No code written yet.

## Context

Issue #271 (split from #253) asks two things:

1. **True RF coverage rings** — *already delivered.* Both the ADS-B and AIS
   dashboards have `ReceptionOutline` ("Reach"): it keeps the furthest contact
   per 10° of bearing and draws the real reception shape, persisted per observer
   location across sessions. That is true coverage, and a richer view than a
   single max-range ring. No work needed unless the reporter specifically wants
   a plain circle too (a ~10-line optional add using the existing MAX NM stat).
2. **Aircraft → airband audio** — *this plan.*

The ADS-B dashboard already has a working **airband** receiver panel
(`airbandFreqSelect`, `updateAirbandFreq()`, `startAirband()`, squelch/volume,
signal meter) and a per-aircraft **details panel** (`selectedIcao`,
`showAircraftDetails()`). What's missing is a link between a contact and "the
relevant frequency."

## The hard part: aircraft don't carry a frequency

ADS-B has no ATC-frequency field. "The relevant airband frequency" has to be
derived from the aircraft's **position** → the nearest airport's tower/approach
frequency. That needs an airport + ATC-frequency dataset, which the repo does
not have today. (Enroute/centre frequencies are not in airport datasets, so the
honest scope is *nearest-airport tower/approach*, best near airports.)

## Proposed approach (MVP: nearest-airport TWR/APP → tune the airband panel)

**Data — OurAirports (public domain):**
- Use `airports.csv` + `airport-frequencies.csv` from ourairports.com/data.
- Build a **trimmed, bundled** data file at prep time: only airports that have a
  `TWR`/`APP`/`GND` frequency, each with `ident`, name, lat/lon and those
  frequencies. Expected size a few hundred KB — ship it under
  `static/data/atc_frequencies.json` (or `.csv`), offline-friendly. A small
  builder script (`scripts/build_atc_frequencies.py`) documents how it was made
  and lets it be regenerated.
- Licensing note: OurAirports data is public domain; attribution is polite.

**Backend — `routes/adsb.py`:**
- `GET /adsb/nearest-airband?lat=<>&lon=<>` → loads the dataset once (module
  cache), returns the nearest airport(s) within a sane radius (e.g. ≤ 60 nm) and
  their TWR/APP/GND frequencies, sorted by distance. Bounded response.

**Client — `templates/adsb_dashboard.html`:**
- In the aircraft details panel, add a **"Tune ATC"** control shown when the
  selected aircraft has a position. It calls `/adsb/nearest-airband` for that
  aircraft's lat/lon, lists the nearest airport's TWR/APP frequencies, and on
  pick sets the airband frequency (`airbandFreqSelect`/custom) and calls
  `startAirband()`. Reuses the existing airband receiver end to end.
- Degrade gracefully: no airport within range → "No ATC frequency nearby."

## Explicitly out of scope (for this MVP)
- "Show the last heard transmission" for an aircraft — needs associating
  demodulated airband voice with a specific contact; not tractable without a lot
  of new infrastructure. Note it in the issue as a non-goal for now.
- Enroute/centre frequencies (not in airport datasets).

## Verification
- Unit test the nearest-airport lookup (a fixture dataset + a known lat/lon →
  expected airport/frequency).
- Route test for `/adsb/nearest-airband` (200 + shape; empty when far from any
  airport).
- Smoke test already loads the ADS-B dashboard; add a small Playwright assertion
  that "Tune ATC" populates the airband frequency for a positioned aircraft.
- Manual: click a real aircraft near an airport, confirm it tunes tower/approach.

## Open questions for approval
1. **Client-side bundled JSON vs. server endpoint?** Recommend the **server
   endpoint** — keeps the dataset off the initial page load and the nearest
   lookup server-side. (Client-only is possible but adds page weight.)
2. **Dataset scope:** TWR + APP only, or also GND/ATIS/CTAF? Recommend
   **TWR + APP + ATIS** (most useful to listen to), GND optional.
3. **Radius cap** for "nearest airport" (default 60 nm?).
