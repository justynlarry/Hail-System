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
- Nightly cron, with failure alerting through Irin
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
- Cloudflare tunnel

**Done when:** someone other than the developer can log in, find last spring's
worst hail, and download it as a spreadsheet.

This phase has standalone value. Knowing where hail hit is useful whether or not
a single email ever goes out.

---

## Phase 3 — Listings
**~15–25 hrs · 2–2.5 weeks**

- RentCast client with pagination and error handling
- Pre-pull estimate: zip count and projected call count shown before confirming
- `api_pulls` / `api_call_log` written on every pull
- "Pulled recently" warning per zip
- Properties, listings, realtors upsert logic
- Storm→listing matching with distance and radius recorded
- Master-detail UI: storms left, matched listings and agents right

**Done when:** selecting a storm and clicking pull returns a reviewed list of
listing agents, and the call count matches what was estimated within reason.

---

## Phase 4 — Accounts
**~10–15 hrs · 1–1.5 weeks**

- Users, password hashing, sessions
- Three roles enforced server-side, not just hidden in the UI
- Admin can add and remove users and nothing else
- Deactivate rather than delete

**Done when:** a viewer account can browse and export but cannot trigger a pull.

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
