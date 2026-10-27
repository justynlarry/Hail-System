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

- Production host (HP ProDesk 400 G6, `pve-prod01`): BIOS (VT-x, AC power recovery, admin password)
- Proxmox VE install, Rocky VM (`rbi-hail01`, VM 101); a separate Dell OptiPlex 3020 runs Proxmox
  Backup Server (`pbs-prod01`) instead of a PBS VM
- *Status 2026-10-20:* both hosts built and through `scripts/hw-acceptance-test.sh`; the VM is created
  but not started
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

**Closed 2026-09-24.** First marked closed 2026-09-23, then held open a day
for follow-ups found after the close. The done-when is demonstrated: signed in
as the `testview` viewer account, Pull shows greyed out; and with a valid CSRF
token a viewer session gets 403 on `/pull`, `/pull/estimate`, `/match` and
`/admin/`, and 200 on `/` and `/export.csv`. The valid token matters: without
it the request fails CSRF before it reaches the role check, and a 403 would
prove nothing about roles (decision log, "Phase 4's done condition verified";
parking-lot item 58).

**All four items above complete 2026-09-22.**

Landed 2026-09-22: `role_required` on `/pull/estimate`, `/pull` and `/match`,
with `can_pull` gating the Pull and Match actions on `storms.html`; the
`/admin` blueprint (add, deactivate, reactivate, change role, sign out, reset
password per user; sign out everyone; settings with change history); forced
logout by timestamp (`sql/018`); last-admin protection (`sql/019`); radii
moved into the `settings` table (`sql/020`); self-service change-password;
and CSRF protection on every POST. Decision log, 2026-09-22 entries.

Landed 2026-09-23: `match_runs` and "Matched, none in range" (`sql/022`,
item 40); the re-pull link (46); RentCast billing day, quota and usage with
warn-and-allow on the pull estimate (`sql/023`, 50); CSRF failures as a 400
page (59); self-action refusal on the admin page (60); and the header, admin
table and disabled-action CSS (67–69). Item 61 was declined. Decision log,
2026-09-23 entries.

Landed 2026-09-24, closing the phase: unreadable RentCast responses now fail
loudly with an attempt count, and every aborted zip's calls reach
`api_call_log` (item 88); the storm list pages at 50 days (86); the script
radius defaults are kept, with the reason recorded (62); and the activity
feed's names and the RentCast billing boundary are recorded as decisions (93,
87's count half). Decision log, 2026-09-24 entries.

Carried forward, not blocking: parking-lot items 47 (stale-pull sweep) and 57
("Pulling…" state), both built 2026-09-25 with gaps listed in the items, 65 (`create_user.py`), 66 (username convention), 85 (UTC
times on the estimate page), 87's boundary half (after 2026-10-09), 89–91,
and 99 (logging unconfigured), which should land before background pulls are
relied on.

---

## Phase 5 — Email
**~25–40 hrs · 3–4 weeks · the long pole**

**Current phase** (Phase 4 closed 2026-09-24). **Begun 2026-09-23.**

Starting point: `send_log`, `email_templates` and `dnc_list` exist from
`sql/007`. At the start `send_log` and `email_templates` were empty, `dnc_list` was
empty, and there was no sending code and no sending identity. Provider chosen
2026-10-06: Constant Contact (decision log; item 119 closed by decision, not yet
confirmed in writing). **State on 2026-10-14:** `dnc_list` holds 760 suppressions;
a send path is built and rehearsed on `hail-dev` but has never sent to a real
recipient (see the 2026-10-06 to 2026-10-13 progress below).

**Progress, as of 2026-10-05 — groundwork only, nothing sends** (bullets are in the order they landed; later ones follow the 09-28 items):
- **CSV exports built** (parking-lot item 92): the matched listings for one
  storm, a bulk range export, and the realtor list (sender/admin only). They
  exclude suppressed agents by default and say a CSV is a snapshot; that is a
  convenience, and the send-time check is still the only real protection.
- **The match page and activity panel** were tidied ahead of the first real
  batch: aligned columns, condensed, pulls and match runs side by side.
- **A responsive CSS pass** for phones and tablets, in review on the
  `responsive-css` branch and not yet recorded as checked in a browser
  (item 114).
- **Pull-flow fixes ahead of the first real batch:** a live "Pulling..." status
  and pull banner, Pull and Match returning to the filtered list, a startup
  sweep for pulls and match runs a restart leaves half-done (items 47, 57,
  118), and a guard so a bad RentCast value can't abort a pull (item 116).
- **Address identity built, 2026-09-26** (item 55): `properties.address_key`,
  generated from a PostGIS `address_standardizer` parse, identifies the
  RentCast duplicate-address problem — 21 groups, 42 properties found so
  far. Not yet acted on: nothing dedupes at match or send time yet, so an
  agent can still get two emails about one house today.
- **RBI's DNS, sending-domain, and provider research** (items 96, 119, 122):
  the domain, its live Microsoft 365 mail, a free sending subdomain, and a
  path to delegate just that subdomain to Cloudflare without touching the
  rest of RBI's DNS; every mainstream transactional provider's policy
  checked directly, none currently permit this use case as-is; Constant
  Contact's own "implied consent" framework checked and found to fit
  better; the current DNC list, RBI's client/service list, and a Constant
  Contact export cross-referenced, surfacing a ~1,014-person warm-start
  pool clear of DNC. Self-hosting reassessed against that smaller pool —
  no decision made on a sending path yet.
- **DNC import built and run, 2026-09-28** (`CLAUDE.md`'s non-negotiable
  rule and this checklist's own "before any send" line — there's no
  dedicated numbered item for it; documented in `docs/decision-log.md`,
  "DNC import: admin upload, staged and previewed before commit"): an
  admin can upload a Constant Contact export, preview
  exactly what would be added before anything is written, and commit or
  discard. `dnc_list` now holds 759 real suppressions (719 from the current
  Constant Contact export, ~40 legacy-only from Airtable), and 108 of the
  hail system's 7,082 realtors are suppressed — up from 83 against the
  Constant Contact list alone, which is the concrete case for having
  imported both sources. **Still open:** whether the Constant Contact
  export itself is missing roughly nine months of unsubscribes (item 129),
  and the suppression check still doesn't run anywhere, because nothing
  sends yet.
- **Merge-field vocabulary (item 16) provisionally settled, 2026-09-28**
  (`docs/decision-log.md`, "Email copy and merge fields: first draft, not
  final"): a first draft of the actual email copy, the required/optional/
  unused field split, and the visible-failure design for a missing
  required field. **Blocked on final copy** — the draft is short on
  purpose but still missing the testimonials, offer list, and images
  expected before any real send (item 135).
- **Append-only enforced in the database, 2026-09-30** (`sql/029`, `030`,
  `sql/guard_test.sql`): triggers on `send_log` and `email_templates`, with
  `DELETE` and `TRUNCATE` revoked from `hail_app`. Applied to `hail-dev`, 18 of
  18 checks passed; **not checked on the production box**. The table owner can
  still `TRUNCATE`, accepted (decision log 2026-09-30).
- **Address search, 2026-10-01** (`sql/031`, `/search`): informational only,
  geocoded through the Census Geocoder. It does not touch the send path.
- **Access and hardening, 2026-09-30 to 2026-10-02:** `hail-dev` is reachable
  through a Cloudflare Tunnel with Access in front; the login hook denies by
  default; CSV formula injection and export filenames fixed; one running pull
  per storm (`sql/033`); `tiger` off the application roles' `search_path`
  (`sql/032`); dark mode.
- **Not started as of 2026-10-05 (the list that was still to do then; most of it is now built, see below):** the sending identity and
  provider, template CRUD, the suppression check in the send transaction, the
  frequency cap, the send queue, bounce handling and warmup. Before the first
  send: settle whether the Constant Contact export is missing about nine months
  of unsubscribes (item 129) and final email copy (item 135). The provider is
  chosen (Constant Contact, 2026-10-06); its token table and OAuth flow were the
  next build (items 168, 169, 170).

**Progress, 2026-10-06 to 2026-10-13 — the Constant Contact send path, built and rehearsed on
`hail-dev`. Nothing has sent to a real recipient:** every `sent_emails` row (7) is a rehearsal to a test
address, and the production box has none of this.
- **Provider access** (items 168 to 170, 181, 182): the OAuth flow and encrypted token table (`sql/034`,
  admin `/cc`), and a spike on the free trial that established how contacts are added and that the API will
  silently re-subscribe an unsubscribed contact, so the code reads before it writes.
- **The unsubscribe sync** into `dnc_list` (`sql/035`, `036`; `POST /cc/sync`), with a log of runs and a
  list of conflicts, meaning an address Constant Contact has unsubscribed whose `dnc_list` row an admin
  removed. Built and wired; only an optional nightly timer is not.
- **The send design** (item 180): one email per realtor per send listing every affected property;
  `sent_emails` holds the email as sent (`sql/037`) and `send_log` one row per match, pointing at it.
  The send needs a good sync within 15 minutes, re-checks `dnc_list` immediately before each call, and commits
  the `send_log` row first. One email per house, not per property record (item 55, 2026-10-13).
- **Eligibility and the cap** (`sql/038`): a storm older than `match_max_age_days` is not emailed about, and a
  realtor emailed within `email_cap_days` is held back unless the sender overrides it. Both are settings
  on the admin page.
- **The `/send` screens**, admin only: preview, confirm and start, recent sends and per-send progress, resume
  and discard of a queued remainder (item 199), and a bounce check (`sql/039`) that records bounces in
  `send_log` and writes hard bounces to `dnc_list`.
- **A call counter** (`sql/040`, item 191): the engine counts every Constant Contact request and stops at the
  10,000-a-day limit, and the send screens show the count.
- **Templates in the UI** (item 202, `sql/041`): a list and a choice on the send page, with a description
  that can be edited until the template has been used.
- **Rehearsals** on `hail-dev` with a mandatory allow-list of test addresses (items 180, 198, 199).
- **Not built, and still the work of this phase:** the production account and sending identity (items 96,
  183), the eligibility gate (item 189), the template builder (item 203), drip campaigns (item 204), a warm-up
  schedule and the first real batch. Before the first send: settle whether the Constant Contact export is
  missing about nine months of unsubscribes (item 129) and final email copy (item 135).

- Sending identity: subdomain, SPF/DKIM/DMARC — **trial account only, self-authenticated; production identity open** (items 96, 183)
- Provider selected (Constant Contact, 2026-10-06) and verified as permitting this kind of outreach (still unconfirmed in writing; item 119)
- Template CRUD, versioning, merge-field validation on save — **partly**: versioning and merge-field validation exist; the UI has a list, a choice and a description editor, not a builder (items 202, 203)
- DNC import (legacy lists) — **done 2026-09-29, 759 suppressions**; completeness is item 129, **settle it before any send**
- Suppression check in the send transaction — **built** (re-checked before each call; the provider call cannot share a transaction, decision log 2026-10-06)
- Frequency cap with a hard floor in the database — **partly**: `email_cap_days` (1 to 90, a `CHECK`) is applied when the send list is built, with a sender override; no database constraint stops a send inside the window
- Send queue with throttling — **built** (queue, resume, discard; the daily Constant Contact limit); the warm-up ramp is not
- Bounce and complaint handling → automatic DNC writes — **bounces built** (hard bounces write to `dnc_list`); the bounce check does not handle complaints
- Warmup schedule: deliberately low volume, ramping — **not built**

**Done when:** a small real batch sends, bounces are captured and suppressed
automatically, and a suppressed address cannot be sent to by any path.

Calendar time here is partly not under our control. Sending reputation builds by
sending modest volume over consecutive days.

---

## Phase 5b — Radar layers on the territory page (internal)
**Started early, 2026-10-20 (decision log 2026-10-20): the rest of Phase 5 is blocked outside our hands. Estimate: several sessions.**

Decision log 2026-10-14 ("Radar layers on the territory page") and 2026-10-20.
- Verify which `MESH_Max_1440min` file covers a Denver day (item 211's kind of question)
- Schema: daily radar-hail polygons, warning polygons with hail tags, tier settings on the admin page
- Nightly ingest of both (ingest image rebuilt); backfill from 2020-10-14
- Tiers as a per-day view: core, margin, radar-indicated, outer
- `/map/radar.geojson` and four layers in `map.js`, with legend and toggles; fix item 216 in the same
  edit
- Staff only: no matcher change, nothing radar-derived in email or exports
- A findings panel per storm day, from our own data; thresholds and margins as versioned parameter sets with
  a repeatable calibration run (inspection data joins later, items 207 and 208)
- Hail only. No new Colorado or Denver constants: time zone, state and map bounds come from settings and data
  (item 238 lists the ones that already exist)

**Progress, 2026-10-22 (on `hail-dev` only):** slices 0 to 5 are built and tested. Slice 0 settled which
`MESH_Max_1440min` file covers a Denver day (decision log 2026-10-21). Slice 1: the GRIB2 decoder (8- and 16-bit
packing) and fetcher. Slice 2: the zone builder (core, margin, radar-indicated) and `radar_day.py`, checked
against an independent implementation on four storm days. Slice 3: NWS warning polygons and the outer layer,
the `storm_states` setting. Slice 4: the nightly and weekly jobs, backfill, `status.sh radar` and the Storm
Days page line, the ingest image rebuilt; the backfill from 2020-10-14 ran 2026-10-22 (2,197 days held, 2 absent at
NOAA) and the timers are installed. Slice 5: `/map/radar.geojson`, the four map layers with a day picker, toggles and
legend on the Territory page, the circle radius from the setting (item 216), and a default report type of HAIL on Storm
Days, Territory and the map. A plausibility ceiling (10 in) sets aside days with impossible MESH values (item 247, built).
**Slice 6 built 2026-10-23** (items 251, 252): a findings panel under the Territory map for the picked radar day (`/territory/findings`,
`hailsys/queries/findings.py`): the day at a glance, reports against the layers, the report circles against the radar, and a
matrix of held houses by radar layer against distance to the nearest actionable hail report, with the draft tier letters as a labelled
annotation; flags for suspect, older-set and no-radar days, no reports, and cold-season unreported radar hail. Staff only, read only.
**Slice 7 built 2026-10-26** (items 243 and 256): the admin page's Hail zones section edits the radar threshold, margin and radius, the CoCoRaHS
size floor, the distance edges, the 20-level grid and `storm_states` as new append-only sets (`sql/056`), with a history, "use these values again" and a
"try it on a day" button. **Not built:** the rebuild button (a command the page names) and the calibration run; slice 8 (inspection data, when RBI delivers it).

**Done when:** for a historical storm day, the four layers draw on the territory page and agree with
the 2026-10 analysis for that day (`docs/analysis/radar-areas-2026-10/`). The tier settings change the
layers from the admin page. Nothing radar-derived reaches an email or an export.

---

## Phase 5c — The hail-likelihood map and the unified pull
**Design accepted 2026-10-23; step 1 waived in (decision log 2026-10-23, "Phase 5c starts"). Step 1a built 2026-10-23 (the CoCoRaHS ingest) and step 1b 2026-10-24 (the likelihood zones and their job); the map layer 2026-10-25.** Steps 2 to 4 each need their
own waiver entry first.

The design is the decision log entry "Design: one hail-likelihood map per storm day, one pull from it" (2026-10-23), with the developer's
answers to its six open questions.
1. **The map (started, internal; the Territory map layer built 2026-10-25):** the CoCoRaHS nightly ingest (built and backfilled for Colorado 2026-10-23, `sql/053`,
   `hailsys/cocorahs/`; item 253 settles the source), likelihood zones per storm day (Agree, One
   source, Context; built for every loaded radar day 2026-10-24, `sql/054` and `055`, `hailsys/likelihood/`), a staff-only layer on the Territory map (built, `/map/likelihood.geojson`), a versioned likelihood parameter set editable on the admin page (built 2026-10-26, slice 7), and the backtest
   with one source hidden (bar about 80%; ran 2026-10-25 and passed on the zip-overlap measure: IEM 88.7%, CoCoRaHS 93.5%, item 255).
2. **Pulls (2a, the estimate compared with the zones and no pulls, waived in and built 2026-10-26, `/pull/zones`; 2b, the pull itself, built 2026-10-27: `sql/059`, `start_pull(zone=...)`, `POST /pull/zones/start`, first real pull 2026-10-27):** the zip list from the union of zips overlapping the chosen levels (default Agree and One source; Context opt-in), any
   overlap at all, one pull per storm day, `pull_zips (pull_id, zcta5, basis, best_level)`, the estimate shown by level.
3. **Matches (waived in 2026-10-26 together with 2b, backtest passed; built 2026-10-27: `sql/057`, `058`, `zonematch.py`, the button on `/pull/zones`; basis has four values, report/radar/observer/both):** Option A, `storm_listing_matches` extended with a nullable `iem_id` and `basis`, `level`,
   `layer`, `set_id`.
4. **Sends by level (waived in 2026-10-27, built and rehearsed on test addresses only; piece 2 of 6 built: the wording rules `hailsys/email/zonewording.py`, the draft `hail_alert_zone` template, and `sql/060` with the pairing trigger and the observer-wording setting):** a zone-kind template and send path beside the report one, pairing enforced in the database, a snapshot of basis and level on each send, response measured per level. Nothing here reaches a real recipient until items 129, 183, 189 and 96 are settled.

**Done when (step 1):** for a historical storm day the likelihood layer draws on the Territory page and agrees with the backtest; an admin can
change the level table and distances and the change is logged; nothing from 5c reaches an email, an export, a pull or the matcher.

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

**Update 2026-10-14.** The question has moved. A 60-storm test (decision log 2026-10-13, *Findings, not a
decision*) found that 90% of actionable hail reports sit on radar hail, and that today's 5-mile circles hold
only 56 to 67% of the radar hail area, with a median of 14% of each circle showing any. Those are findings, not
a decision to adopt MESH: the claim problem above still needs its own decision-log entry first. The proposals
are parking-lot items 205 (ranking by radar) and 206 (a radar-shaped area); the free archives start at
2019-08-20 (Iowa State) and 2020-10-14 (NOAA).

**Trigger:** sparse coverage turning out to be a real operational limit in the
pilot — storms RBI knows happened that the browse cannot show — rather than a
known property of the data we have already accounted for. Phase 6 at the
earliest.

**Narrowed 2026-10-14:** adopted for internal display only, as Phase 5b (decision log 2026-10-14,
"Radar layers on the territory page"). Anything sent outside RBI still needs its own decision.

### Building permits and jurisdiction

Parked. See parking-lot items 70–84, `docs/data-sources.md` §5, and the
decision log's 2026-09-23 entries.

**Trigger:** the system running in production; the jurisdiction and
address-search half also waits on parking-lot item 23 (geocoding).
