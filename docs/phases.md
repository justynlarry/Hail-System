# Phases

Preliminary. Estimates assume 10–15 hrs/week. Total roughly 110–180 hours,
about three months.

Each phase has a **done** condition. If it cannot be demonstrated, the phase is
not done. Do not start the next phase's work early — the point of the sequence
is that each one produces something checkable.

**Running alongside from day one:** the domain and email-sending setup. It is the
only item blocked on RBI rather than on us, and DNS changes at a small company
can sit in someone's inbox for weeks. Start the ask in Phase 0 so it is resolved
by the time Phase 5 needs it.

---

## Phase 0 — Groundwork
**~12–20 hrs · 1.5–2 weeks · nothing visible yet**

- OptiPlex: RAM, second SSD, BIOS (VT-x, AC power recovery, admin password)
- Proxmox install, Rocky VM, PBS VM
- Static IP, UPS, add to Irin monitoring as a client
- PostgreSQL + PostGIS
- Schema created from `database-schema.md`
- ZCTA and county boundaries loaded, reprojected to 4326, GiST indexed
- `report_types` and `sources` seeded from `reference/`
- Repo initialized, `.gitignore` in place before anything large is committed
- **Ask RBI for DNS access and existing subscription status**

**Done when:** a spatial query returns the zip codes within 5 miles of an
arbitrary lat/lon.

---

## Phase 1 — Storm data flowing
**~15–25 hrs · 2–2.5 weeks · first proof it works**

- IEM ingest script: 30-hour overlapping window, idempotent, `ON CONFLICT DO NOTHING`
- Handle the known traps: literal `None`, unquoted commas, composite type key
- Five-year backfill
- Buffer query: report → affected zips at a configurable radius
- Nightly systemd timer, with failure alerting through Irin
- **Radius vs. coverage test:** how many Front Range zips does 3 / 5 / 10 miles
  actually produce? Settles whether zip-scoped pulls filter anything

**Done when:** a spreadsheet of affected zip codes can be produced for a real
storm from last month, and the nightly job has run unattended for a week.

---

## Phase 2 — Storm browser
**~20–30 hrs · 2–3 weeks · usable on its own**

- Web app skeleton, auth scaffolding
- Storm list: filter by date range, event type, magnitude
- Group results by city, county, or zip
- Confidence label with its inputs shown
- CSV export on every list
- Tailscale (decision-log 2026-09-14, superseding the Cloudflare tunnel named
  here originally; revisit Cloudflare Access at Phase 6)

**Done when:** someone other than the developer can log in, find last spring's
worst hail, and download it as a spreadsheet.

**Closed 2026-09-17.**

This phase has standalone value. Knowing where hail hit is useful whether or not
a single email ever goes out.

---

## Phase 3 — Listings
**~15–25 hrs · 2–2.5 weeks**

**Begun 2026-09-17.**

- RentCast client with pagination and error handling — `hailsys/rentcast/client.py`
- Pre-pull estimate: zip count and projected call count shown before confirming — `hailsys/rentcast/estimate.py`
- `api_pulls` / `api_call_log` written on every pull — schema link in `sql/013_pull_storm_link.sql`, write path in `hailsys/rentcast/pull.py`
- "Pulled recently" warning per zip
- Properties, listings, realtors upsert logic
- Storm→listing matching with distance and radius recorded
- Master-detail UI: storms left, matched listings and agents right

**Done when:** selecting a storm and clicking pull returns a reviewed list of
listing agents, and the call count matches what was estimated within reason.

**Closed 2026-09-21.**

Also landed this phase, beyond the original scope above: a background-thread
pull path (`hailsys/web/jobs.py`) so a pull doesn't block the request; an
activity feed (`hailsys/queries/activity.py`, `/activity`) surfacing new
storm days, pulls, and match runs since a user's last login; derived work
state per storm day (`hailsys/queries/workstate.py`) replacing a stored
status; a single merged header with visible flash messages; and
`report_zip_distances` (`sql/017`), precomputing report-to-zip distances so
`storms.py` stops running a live spatial join on every query.

**`storms.py`'s switch to `report_zip_distances` is deployed and verified.**
`web` bind-mounts `./hailsys`, so the switch went live at the first `web`
restart after `93c7f85` — there is no separate deploy step (see the decision
log, "With `./hailsys` bind-mounted into web, editing is deploying"). The
precondition it was written against holds: `scripts/backfill_zip_distances.py`
finished the full historical backfill (177,515 reports, 2,255,652 pairs, 0
reports without rows), and old-vs-new `PAIRS_SQL` output is identical on three
windows (`scripts/verify_zip_distances.py`, decision log 2026-09-21). Timings
on calendar 2019, previously ~141 s for `/`: `/` recent days 0.06–0.09 s
(actionable) and 0.45 s (all types), zips 0.05–0.06 s, export pairs 0.91 s
(49,592 rows). The hazard the precondition guarded against — the inner join
silently dropping reports not yet backfilled, reading as "no data" — does not
apply while the table covers every report and the `AFTER INSERT` trigger keeps
new ones covered.

---

## Phase 4 — Accounts
**~10–15 hrs · 1–1.5 weeks**

- Users, password hashing, sessions
- Three roles enforced server-side, not just hidden in the UI
- Admin can add and remove users and nothing else — reversed 2026-09-22:
  admin is a full superset of sender (decision log, "Admin is a full superset
  of sender")
- Deactivate rather than delete

**Done when:** a viewer account can browse and export but cannot trigger a pull.

**Current phase** (Phase 3 closed 2026-09-21).

**All four items above complete 2026-09-22.** The phase is not closed: the
done-when has not yet been demonstrated with a real viewer account
(parking-lot item 58).

Landed 2026-09-22: `role_required` on `/pull/estimate`, `/pull` and `/match`,
with `can_pull` gating the Pull and Match actions on `storms.html`; the
`/admin` blueprint (add, deactivate, reactivate, change role, sign out, reset
password per user; sign out everyone; settings with change history); forced
logout by timestamp (`sql/018`); last-admin protection (`sql/019`); radii
moved into the `settings` table (`sql/020`); self-service change-password;
and CSRF protection on every POST. Decision log, 2026-09-22 entries.

Still open in Phase 4: parking-lot items 40 (matched-found-nothing), 46
(re-pull affordance), and 50 (RentCast quota tracker), plus the Phase 4
items filed 2026-09-22 (58–62).

---

## Phase 5 — Email
**~25–40 hrs · 3–4 weeks · the long pole**

- Sending identity: subdomain, SPF/DKIM/DMARC
- Provider selected and verified as permitting this kind of outreach
- Template CRUD, versioning, merge-field validation on save
- DNC import (legacy lists) — **before any send**
- Suppression check in the send transaction
- Frequency cap with a hard floor in the database
- Send queue with throttling
- Bounce and complaint handling → automatic DNC writes
- Warmup schedule: deliberately low volume, ramping

**Done when:** a small real batch sends, bounces are captured and suppressed
automatically, and a suppressed address cannot be sent to by any path.

Calendar time here is partly not under our control. Sending reputation builds by
sending modest volume over consecutive days.

---

## Phase 6 — Pilot
**~10–15 hrs · 2–4 weeks**

- Justyn as sole user, working real storms
- Fix what surfaces
- Runbook written from actual failures, not imagined ones
- Tune the radius and frequency cap against real results

**Done when:** a full month has run without intervention and the send log is
consistent with what actually happened.

Test against **historical IEM dates** rather than waiting for live hail — the
archive goes back to 2003, and wind events are abundant year-round.

---

## Phase 7 — Rollout
**~5–10 hrs · 1 week**

- Staff accounts
- Short walkthrough session
- User manual
- Handoff notes and escalation expectations (business hours, not on call)

**Done when:** someone else in the office has sent a real batch without help.

---

## Not on the roadmap

Ideas recorded so they are findable, not because they are owed. Nothing here
has a **When:** — each has a **trigger** instead, and absent that trigger the
right amount of work to do on it is none.

### Radar-derived hail size (NOAA MRMS / MESH)

NOAA publishes MRMS MESH — a gridded estimate of maximum hail size — free,
with an archive going back years. It would answer the sparse-coverage problem
directly: 382 of 1,468 Denver-local hail days carry exactly one report, and
the HAIL distribution skews to the eastern plains rather than the Front Range
metro. A grid has no such gaps, because it does not depend on someone being
outside and choosing to call it in.

**Why it is not a near-term item, beyond the data volume.** National grids
every two minutes is heavy, and heavier than anything this system currently
touches — but the cost that matters is not storage. **MESH is a radar-derived
estimate, not a report**, and this project's central claim is that a report
was filed. "Hail of this size was reported near this listing" survives a
homeowner, an agent, or an attorney asking where the number came from; "our
radar model estimated 1.75 inches over your roof" is a different sentence
making a different promise, and it is the sentence a contractor-built system
would reach for. Adopting MESH is therefore a change to the product's claim
first and an ingest problem second. It would need its own decision-log entry
on that point alone, and the tiered confidence label would need somewhere
honest to put an estimate alongside human reports without the two blurring.

**Trigger:** sparse coverage turning out to be a real operational limit in the
pilot — storms RBI knows happened that the browse cannot show — rather than a
known property of the data we have already accounted for. Phase 6 at the
earliest.
