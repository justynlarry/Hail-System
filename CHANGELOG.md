# Changelog

One section per release, newest first (parking-lot item 289).  A release is a git tag on `main`, made with
`scripts/release.sh`; production runs a tag, never `main` (`docs/deploy.md`).  Numbers are
`vMAJOR.MINOR.PATCH`:  PATCH for fixes only, MINOR for anything new or changed that people will notice (a page, a
setting, a migration), MAJOR for a milestone (`v1.0.0` is the pilot, Phase 6).  The detail lives in
`docs/parking-lot.md` and `docs/decision-log.md`; this names the items.  The repository is public:  nothing here
names a person, an address or a secret.

## v0.9.0

Not yet tagged:  this is the first release, tagged when `rbi-hail01` is first deployed.  It is everything built on
`hail-dev` through Phase 5c.

### In it
- **Storm data:** the nightly IEM storm-report ingest and archive (Phase 1), zip codes derived on read; NOAA MRMS
  radar layers and NWS warnings (Phase 5b); the CoCoRaHS ingest and the hail-likelihood zones (Phase 5c).
- **Web app:** Storm Days, Territory (map with radar, likelihood and storm-coloured reports), Address Search,
  Activity & Exports, the match page; accounts and roles (Phase 4); the Admin page in tabs.
- **Listings:** RentCast pulls, by storm report or from the hail zones, always a person's click, logged and counted
  against the monthly quota (Phases 3 and 5c).
- **Email:** the Constant Contact send path (preview, review, send, batches, bounce check), the do-not-contact list
  checked at send time, sends by report or by hail-zone level, drip follow-ups, and the template editor (items 202 to
  204, 294).  Rehearsed on test addresses only; no real recipient until items 183, 189 and 96 are settled.
- **Sign-in:** the session cookie is Secure and SameSite=Lax; a sign-in lasts "Sign out after" hours (admin page,
  default 12); five wrong passwords for a name in 15 minutes refuse it for up to 15 minutes; every attempt is
  logged in `login_attempts` (item 299).
- **Operations:** migrations by `scripts/migrate.sh` (item 277), `scripts/status.sh`, versioned static files
  (item 296), the release stamp in the footer (item 289), every package pinned (item 300), and `status.sh images`
  flagging a running container on an old image (item 302).
