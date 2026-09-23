# CLAUDE.md

Project context for Claude Code. Read `database-schema.md` before touching the database.

## What this is

A storm outreach system for Roof Brokers, Inc. (RBI), a Front Range roofing
contractor. It pulls free NWS storm reports nightly, maps them to affected zip
codes, lets staff pull real estate listings in those areas from RentCast, and
send templated email to the listing agents.

Single developer. Two machines — a `hail-dev` VM and the production
OptiPlex, deliberately built alike. See `docs/hail-consolidated.md` §9.

## Current phase

**Phase 5 — Email.** Phases 0–4 are closed: 0 on 2026-09-03 when
a spatial query returned the zip codes within 5 miles of an arbitrary
lat/lon, 1 on the IEM ingest and archive backfill going live, 2 on
2026-09-17 with the storm-browser web app (Flask, Tailscale-reached) and its
CSV export built and running, 3 on 2026-09-21 with RentCast pulls,
storm-to-listing matching and the match page working through the web UI, and
4 on 2026-09-23 when a viewer account could browse and export but not pull.

**Caveat on "Phase 0 closed":** that call was made against Phase 0's own
done-when bar — the spatial query — not against every item Phase 0's task
list carried, and the physical production OptiPlex build (racking it at
RBI's office, Proxmox, static IP, UPS, Irin enrollment) is one of those
items. Everything verified so far has run on the `hail-dev` VM. Do not
assume the production box is racked, reachable, or has anything deployed to
it without checking.

What Phase 3 delivered: `hailsys/rentcast/` (`client.py`, `estimate.py`,
`pull.py`, `upsert.py`); `sql/013`–`017` (linking a pull back to the storm it
was pulled for, `properties.geom`, match attribution, an `ingested_at` index,
and `report_zip_distances`); `hailsys/matching/matcher.py` writing
`storm_listing_matches`; and the UI for it — `/pull/estimate`, `/pull`,
`/match`, `/storms/matches` and the activity feed. A pull runs in a background
thread and matches automatically when it finishes. No sending path exists.

What Phase 4 delivered: roles enforced server-side —
`role_required("sender", "admin")` on `/pull/estimate`, `/pull` and `/match`,
`login_required` on every other signed-in route, and the `/admin` blueprint's
own `before_request` admin check. The UI greys actions a role can't take
rather than hiding them. `/admin` covers users (add, role, sign out,
deactivate, reactivate, reset password; not on your own row) and the
`settings` table: radii, RentCast billing day and monthly quota, and this
period's usage, with every change logged to `settings_history` by trigger.
Also: forced logout by timestamp (`sql/018`), last-admin protection
(`sql/019`), radii in `settings` (`sql/020`), `match_runs` so an empty match
reads "Matched, none in range" (`sql/022`), quota settings (`sql/023`), a
re-pull link, quota warn-and-allow on the pull estimate, and CSRF on every
POST (a failure returns a 400 page). `sql/021` (`municipal_boundaries`) is
from the parked permits research, not Phase 4. `scripts/create_user.py`
still exists; whether it stays for bootstrapping is `docs/parking-lot.md`
item 65.

What exists for Phase 5 so far: `send_log`, `email_templates` and `dnc_list`
from `sql/007`, all empty, and `workstate.py` reading `send_log` for the
"Sent" state. No sending code, no provider chosen, and the legacy DNC lists
not yet imported — that import must land before any send.

Phases in order: 0 groundwork → 1 IEM ingest + zip mapping → 2 storm browser
with CSV export → 3 RentCast listings → 4 accounts → 5 email → 6 pilot →
7 rollout.

Do not build ahead of the current phase.

## Stack

- Rocky Linux VM on Proxmox, single Dell OptiPlex, on RBI's office network
- PostgreSQL + PostGIS
- Python backend
- Web UI, reached over Tailscale for Phase 2 (Cloudflare tunnel revisited at
  Phase 6 — see `docs/decision-log.md`, 2026-09-14)
- Deployed with Ansible where practical

## Non-negotiable rules

**Nothing sends email automatically.** There is no code path from the nightly
ingest to an outbound message. A human clicks send. Do not add scheduled sends,
auto-followups, or "helpful" automation around sending.

**The suppression check runs against `dnc_list` at send time, in the same
transaction as the send.** Not in the UI, not from a cached list, not from a
flag on the realtor row. If a rule must hold, it holds in the database.

**Storm reports are never deleted and never collapsed to zip codes at write
time.** Full lat/lon fidelity in, zips derived on read. The buffer radius is a
tuning parameter.

**`send_log` and `email_templates` are append-only.** Templates are superseded,
never edited. Sends are inserted, and only their provider status is updated
afterward.

**All timestamps are `TIMESTAMPTZ` stored in UTC.** Convert to `America/Denver`
at display only.

**RentCast calls cost money.** Never add a call to a code path that runs
automatically or on page load. Every pull is explicitly user-initiated and
logged to `api_pulls`.

## Known data traps

These have already bitten us. Do not re-discover them.

- IEM sends the **literal string `None`** as its null marker for magnitude.
  Coercing it to 0 produces 549 magnitude-zero tornadoes.
- IEM `TYPECODE` is **not unique** — `R` is both RAIN and HEAVY RAIN. Keys are
  `(report_type, report_text)`.
- Some IEM CSV rows have **unquoted commas inside the CITY field**. Never split
  on commas. But a real CSV parser only **detects** these — it cannot repair
  them, because the quotes were never written and the field boundary is
  unrecoverable. They are rejected as `field_count_mismatch`; `raw_row` keeps
  the line verbatim, which is what makes rejecting non-lossy.
- **`Decimal()` accepts `'NaN'` and `'Infinity'`** without raising. A NaN
  coordinate then makes an ordered comparison *signal* `InvalidOperation`, so a
  range check raises and the exception escapes the parser and ends the run. And
  Postgres `NUMERIC` accepts `NaN`, so an unguarded magnitude lands in the
  column and reads as a real measurement. Guard with `is_finite()` before any
  range test.
- **A misspelled IEM filter parameter is silently ignored, not rejected.**
  `typetext=` and `magnitude=` return the full unfiltered set with HTTP 200;
  the real names are `type=` and `magge=`. (We must not filter at ingest
  anyway — this is a trap for anyone reading the old docs.)
- IEM `QUALIFIER` of `M` (measured) on hail **does not mean instrument-measured**
  — it tracks reporter training. Use `SOURCE` if a confidence signal is needed.
- Census TIGER ships in **NAD83 (4269)**; IEM and RentCast are **WGS84 (4326)**.
  Reproject at load. Mixing them fails silently.
- RentCast `id` is a **property** id, not a listing id. A relisted house reuses it.
- RentCast agent email is **frequently missing**. Handle null.

## Conventions

- Ask before installing anything not already present.
- Prefer stdlib and boring dependencies.
- Scripts that ingest external data must be idempotent and safe to re-run.
- Failures should be loud. Silent partial success is worse than an error.
- Comment the *why*, not the *what*, especially around the traps above.

## Ask before editing

Verification questions are not work requests. "What's the status of X",
"is Y still open", "does the log have an entry for Z" ask you to read and
report, not to fix what you find. Report what's there, name what's missing,
and stop.

Propose the change and wait for a yes before editing any file — code, SQL,
or docs. This holds even when the fix is obvious and already agreed in
principle, because priorities shift between the decision and the moment,
and an item that was next last week may have been deprioritized since.

This also holds when something is actively broken. "Can you take a look"
at an error is a request to diagnose, not a standing invitation to patch
it — name the bug and the fix, then stop and wait, the same as any other
finding. An outage doesn't waive the rule; it's not an emergency-fix
exception, it's the default for every edit, with no case-by-case judgment
call about how obvious or urgent the fix seems.

## Working style

The user is teaching himself as this is built — Bash, Docker, Python, Postgres.
Explain reasoning briefly when making a non-obvious choice. Do not silently
refactor working code. Do not add features that were not asked for.

Plan first, build second.
