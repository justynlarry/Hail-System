# Deploying to a new server

The ordered checklist for taking a bare VM to a server that is ready to send. Each step says what to do and where the
detail is; `docs/server-setup.md` holds the detail for the OS and database steps, and this file does not repeat it.

**Keep this file current.** When a deploy step changes (a new migration rule, a new service, a new secret, a new
check), change it here in the same commit. The production build is the first real use; whatever it finds that this
file got wrong is fixed here, not only in the moment.

Two rules for every server:

- **Never copy secrets or data from `hail-dev`,** except the suppression list (step 7). Every password and key in
  `.env` is new. Constant Contact grants, rehearsal sends and test templates never move.
- **Never run the database tests on production.** Most roll back, but a few commit test rows and delete them, and
  some tables can never be deleted from. Production is checked with the schema diff, `scripts/status.sh` and the
  page checks in step 10. The tests run on `hail-dev` and in throwaway containers.

## rbi-hail01: where it stands

**Release running:** `v0.10.0` (`681bfd8`), checked out 2026-11-09; the footer reads `v0.10.0 · 681bfd8` (items 289, 311). Production runs a tag, never `main`, and
its footer shows the release (for example `v0.9.0 · abc1234`); update this line at each deploy.

Update this table as the build moves. Item numbers are in `docs/parking-lot.md`.

| Step | State (2026-11-06) |
|---|---|
| 0. Before you start | Production Constant Contact account (183) and sending domain (96) not yet set up; host alerts set up 2026-11-09 (236) |
| 1. Hosts | `pve-prod01`, `pbs-prod01` built and acceptance-tested 2026-10-19; notifications working 2026-11-09 (236), retention set 2026-11-06; sshd hardened 2026-11-09, internal DNS accepted (237); the second backup disk (233) open |
| 2. OS | Rocky 10.2, running |
| 3. Code and files | `v0.9.1` checked out 2026-11-05 (the tag pushed from `hail-dev`, item 306); the TIGER folders copied |
| 4. Secrets | `.env` filled in, except the Constant Contact values (placeholders until item 183) and the domain (96) |
| 5. Build and start | Built and started 2026-11-05; `status.sh images` all OK |
| 6. The database | `migrate.sh --apply` from empty, 2026-11-05: `recorded 67, pending 0` (item 305) |
| 7. Data | First admin, reference and coverage loaded; suppressions copied 2026-11-05, 768 active and 1 removed, the same as `hail-dev` (item 285); backfills done 2026-11-06, every count the same as `hail-dev` (IEM, radar, radar zones, warnings, CoCoRaHS, likelihood); the eight timers enabled, the four nightly ones ran 2026-11-06. settings checked 2026-11-06, all as wanted (item 308). **Open:** the other users, the permission list (item 189) |
| 8. Access | `tailscale serve` for the developer, signed in 2026-11-05. The staff Cloudflare tunnel not set up (waits on the domain, 96) |
| 9. Constant Contact | Not started (item 183) |
| 10. Checks | `status.sh`: every nightly job OK 2026-11-06; `status.sh images` OK. Backup and restore test passed 2026-11-06, backups scheduled. Role sign-ins checked 2026-11-06 (item 309). **Done** |
| 11. Before the first real send | Items 183, 189, 96, 135 open |

---

## 0. Before you start

Have these in hand, or know where they come from:

- [x] **Name and address:** hostname, static IP, gateway, DNS. For `rbi-hail01`: `docs/server-setup.md`, Production
      hosts, and `docs/hail-consolidated.md` §9.
- [x] **Access:** a Tailscale account to join the server to, and your SSH key.
- [x] **RentCast key:** the same RentCast account as `hail-dev` (decided 2026-11-02). `hail-dev` makes no real pulls
      once production is live (developer, 2026-11-06), so only the first billing period's count on production misses
      `hail-dev`'s calls, accepted.
- [ ] **Constant Contact:** the production account's API key and secret (item 183), and the redirect URI on the
      production hostname, registered in that account's app settings.
- [ ] **Cloudflare:** a tunnel and hostname for this server, separate from `hail-dev`'s (decision log, "Tunnel
      topology", item 110): `app.roofbrokersinc-weather.com` for production.
- [x] **Alerts:** an SMTP account for host notifications (item 236; a dedicated Gmail account with an app password
      was proposed).
- [x] **Documents reviewed:** every file in `docs/` checked against the repo and the database, and brought up to date,
      before the release is tagged (developer, 2026-11-04; items 212 and 298). Nothing updates them when features land,
      so this review is what keeps them current. `docs/hail-consolidated.md` is the handoff document: re-sync it and
      its "Last synced" line. Ask Claude Code to "review all the .md files in docs".
      Done 2026-11-04 (before `v0.9.0`) and again 2026-11-06, after the first deploy: `CLAUDE.md`, `README.md` and every
      file in `docs/` except the dated analysis reports, checked against both machines; `hail-consolidated.md` re-synced
      to 2026-11-06 (`18c563d`; item 310 came out of it). Repeat it before each release (§12).

## 1. The hosts (when the server is a Proxmox VM)

Done on the Proxmox and backup hosts, before the VM is started or joins Tailscale (item 237).

- [x] Tailscale key expiry off on both hosts; Tailscale SSH off on both. Done 2026-11-06 for `pve-prod01`, `pbs-prod01`
      and `rbi-hail01` (key expiry off on all three; `"RunSSH": false` on `pve-prod01` and `rbi-hail01`, `pbs-prod01`
      off since 2026-10-20).
- [x] Remove any stale node with the VM's name from the tailnet it will join (a MagicDNS name collision otherwise).
- [x] sshd: keys only, no root password (`PermitRootLogin prohibit-password`, `PasswordAuthentication no`,
      `KbdInteractiveAuthentication no` in a drop-in under `/etc/ssh/sshd_config.d/`). Prove key login works first,
      keep a session open, `sshd -t` before reloading. Done 2026-11-09 on both hosts: the drop-in `10-hardening.conf`,
      after the developer's key sign-in was proven; a probe from `hail-dev` shows `publickey` only.
- [x] A notification target that delivers, and a test notice that arrives (item 236). Done 2026-11-09 on both hosts:
      an SMTP target through the developer's Gmail (an app password per host), the default matcher pointed at it.
- [x] The backup job for the VM, with retention, prune and garbage collection (item 233).
- [x] The VM: CPU type `x86-64-v3` (Rocky 10 needs it), disk with `discard=on` and `ssd=1`, `agent=1`.

## 2. The operating system

Detail: `docs/server-setup.md`, from "Hostname" to "SELinux Configuration".

- [x] Install Rocky Linux; set the hostname.
- [x] Static IP with `nmcli` (the connection NAME, `ipv4.method manual`, `ignore-auto-dns`).
- [x] `sudo dnf update -y`, reboot if the kernel changed.
- [x] `qemu-guest-agent` installed and running; `fstrim.timer` enabled (item 233).
- [x] Timezone UTC; journald persistent with `SystemMaxUse=500M`.
- [x] firewalld left on with nothing opened.
- [x] Podman removed, Docker CE installed from Docker's repository, `hello-world` runs.
- [x] **The account is `hail-user`, UID 1000, and the repo will be `~/hail-system`.** The systemd units name the
      user and the path (`systemd/*.service`), and the `cloudflared` service runs as `1000:1000` to read its credential.
      The first account made in the Rocky installer gets UID 1000; check with `id hail-user`.
- [x] Tailscale installed and joined (`sudo tailscale up`).

## 3. Code and files

Detail: `docs/server-setup.md`, "Git/Github Setup" and "to move files untracked in git".

- [x] git installed, an SSH key for this server added to GitHub, `ssh -T git@github.com` answers.
- [x] `git clone git@github.com:justynlarry/Hail-System.git ~/hail-system`.
- [x] Check out the release, not `main` (item 289): on `hail-dev`, `scripts/release.sh v0.9.0` tags and pushes it (it
      refuses unless `CHANGELOG.md` has its section and `main` is clean and pushed); here, `git fetch --tags` and
      `git checkout v0.9.0`.
      **If GitHub will not take the tag** (item 306: `v0.9.1` was refused with `fatal error in commit_refs`), push it
      from `hail-dev` straight into this server's repo instead, then check it out here as usual:
      ```
      git push ssh://hail-user@<server-tailscale-ip>/home/hail-user/hail-system refs/tags/vX.Y.Z:refs/tags/vX.Y.Z
      ```
      It must go from `hail-dev` to the server, not be fetched from here: `rbi-hail01` is on a different Tailscale
      login (`rbi.justyn@`) and is shared into `hail-dev`'s, and a shared machine can answer connections but not start
      them, so a fetch from `rbi-hail01` to `hail-dev` times out. Git accepts a pushed tag into a checked-out repo (it
      refuses only a push to the checked-out branch). It is the same tag object, so it does not conflict when GitHub
      later takes the push; push it there too once it does.
- [x] `scripts/write_version.sh`: writes `hailsys/VERSION`, which the footer shows after `web` starts (§5).
- [x] From `hail-dev`, rsync the two TIGER folders only, `data/raw/tiger/tl_2025_us_zcta520` and
      `tl_2025_us_county` (about 915 MB; dry run first): the command is in `docs/server-setup.md`, "to move files
      untracked in git". They are all the reference load reads from outside the clone. **Not `reference/`:** it holds
      real suppression exports, and the suppression list moves only by `dnc_copy.sh` (§7). Not the rest of `data/`
      either (item 304).

## 4. Secrets

- [x] `cp .env.example .env`, `chmod 600 .env`, and fill in **new** values. Generate them on the server:
      ```
      python3 -c "import secrets; print(secrets.token_urlsafe(24))"     # each of the three database passwords
      python3 -c "import secrets; print(secrets.token_hex(32))"          # FLASK_SECRET_KEY
      python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # HAIL_TOKEN_KEY
      ```
      `HAIL_TOKEN_KEY` is a Fernet key, which is exactly 32 random bytes in URL-safe base64; the stdlib line above
      makes one without the `cryptography` package (`.env.example` shows the same key made inside the web image).
      Compose refuses to start with any required value empty, so fill every one before step 5.
- [ ] `RENTCAST_KEY`; `CC_API_KEY`, `CC_API_SECRET` from the **production** Constant Contact account;
      `CC_REDIRECT_URI` on the production hostname.
- [x] Use valid shell names only (item 242): `. ./.env` must not fail.
- [x] **A `$` in a value is written `$$`** (item 304). Compose reads `$$` as one `$`; a single `$` starts a variable
      name. The generator lines above never make one. Because of this, read a value back through Compose, never with
      `grep | cut` or by sourcing the file (both pass `$$` as written, and the shell turns it into its process id):
      ```
      PW=$(docker compose run --rm --no-deps -T --entrypoint sh loader -c 'printf %s "$PGPASSWORD"')   # POSTGIS_ROOT_PASSWORD
      ```

## 5. Build and start

- [x] `docker compose build web app ingest loader`
- [x] `docker compose up -d postgis web` and wait for `postgis` to report `healthy` (`docker compose ps`).
- [x] `scripts/status.sh images` reads OK for all four.
- [x] The footer of the login page shows the release (for example `v0.9.0 · abc1234`), not "development": if it says
      "development", `scripts/write_version.sh` was not run (§3); run it and `docker compose restart web`.

The `cloudflared` service waits for step 8.

## 6. The database

Detail: `docs/server-setup.md`, "Database Migrations".

- [x] `docker compose run --rm -T loader bash scripts/migrate.sh` (report: every file pending), then `--apply`, then the
      report again: `recorded N, pending 0`.
- [x] Build the same files in a throwaway `postgis/postgis` container of the same tag, and diff its schema with this
      one: they should be identical (`docs/schema-review.md` §1). Not `guard_test.sql` here: it needs rows that must
      not go on production. Done 2026-11-06 for `rbi-hail01`: its `pg_dump --schema-only` is identical to `hail-dev`'s,
      which was diffed identical to a fresh build on 2026-11-02.

## 7. Data

Detail: `docs/server-setup.md`, "Data on a new server" (decided 2026-11-02). In order:

- [x] The first admin with `scripts/create_user.py`, as `hail_admin`. Every other user on the admin page.
- [x] `scripts/load_reference.sh`, then `scripts/load_coverage.sh` (the loader service).
- [x] Settings on the admin page: check each against what this install should use, and change what differs (radii,
      billing day, quota, freshness, match age, email cap, sign out after (hours; item 299), the email images, sender
      and contact settings, `storm_states`). Only a change is logged to `settings_history`, and an unchanged hail-zone
      set is refused; the starting values are the migrations' defaults. On `rbi-hail01` all matched, checked
      2026-11-06, so the history is empty (item 308).
- [x] **Suppressions:** `scripts/dnc_copy.sh export` on `hail-dev`, copy the file over Tailscale, `scripts/dnc_copy.sh
      load` here, delete the file on both machines. The load's last table must show the same active and removed counts
      as `hail-dev` (item 285; close it then).
- [ ] **Permission list (item 189):** on the admin page, Permission list, upload the final RBI service-client list
      ("RBI service clients") and the Constant Contact subscriber export ("Constant Contact subscribers", Active rows
      only), each dated the day it was exported; check each preview before adding. The counts on the tab must match
      the files. Until this is done the list is empty and nothing can be emailed, by design. Never run
      `sql/dev_permission_seed.sql` here: it is for `hail-dev`'s rehearsal realtors only.
- [x] The public data rebuilt by backfill: IEM from 2004, radar (about 2½ hours), the warnings before the radar (item 307), CoCoRaHS, then likelihood.
- [x] The timers: `sudo cp systemd/* /etc/systemd/system/`, `sudo systemctl daemon-reload`,
      `sudo systemctl enable --now` on the eight `.timer` units.
- [ ] RentCast data starts empty: the first pull is a person's click.

## 8. Access

- [x] **For the developer:** `tailscale serve --bg 8000`; the app answers at `https://<hostname>.<tailnet>.ts.net`.
- [ ] **For staff:** this server's own Cloudflare tunnel, never `hail-dev`'s (two connectors on one tunnel are
      load-balanced replicas). On a machine logged in to the Cloudflare account, `cloudflared tunnel create <name>`
      writes the credential `<tunnel-id>.json`; route the production hostname to that tunnel. Here:
      `cp cloudflared/config.example.yml cloudflared/config.yml`, fill in the tunnel ID and hostname, put the
      credential beside it (mode 600, owned by `hail-user`). Both files stay out of git (item 287). Add the hostname to
      a Cloudflare Access policy (allowed emails, one-time PIN), then `docker compose up -d cloudflared` and look for
      `Registered tunnel connection` in its log.
- [ ] Check the Access application's **session duration** in the Cloudflare dashboard and write it down here. It
      decides how often staff re-enter a PIN; the app's own limit is "Sign out after" on the admin page (item 299). The
      dashboard setting is outside the repo, so nothing else records it.
- [ ] `web` stays bound to `127.0.0.1:8000`; nothing is published on the LAN.

## 9. Constant Contact

- [ ] At `/cc`, connect the production account (the OAuth sign-in returns to `CC_REDIRECT_URI`).
- [ ] Run the first unsubscribe sync from `/cc`; it must complete before any send.
- [ ] The behaviours item 183 lists, checked on this account.

## 10. Checks

- [x] `scripts/status.sh`: each nightly job has run, after the first night.
- [x] `scripts/status.sh images`: all OK.
- [x] Sign in as an admin, a sender and a viewer: each opens what its role may (Storm Days, Territory with the map,
      the match page, `/admin` for the admin only) and is refused the rest. Done 2026-11-06 with `test-sender` and
      `test-viewer` (deactivated after): the menus matched each role, and typed-in addresses were refused with 403 in
      the web log (`/admin/` for both, `/pull/estimate` for the viewer, `/send/` for the sender); no 500s. Send,
      Campaigns and Templates are admin-only (item 309).
- [x] A backup of the VM has completed, and a test restore of it has been booted with the network disconnected
      (item 233; the restore target must support `x86-64-v3`). Done 2026-11-06: snapshot mode with the guest agent,
      restored as VMID 9100, `migrate.sh` reported `recorded 67, pending 0`, and the IEM, radar, likelihood and
      suppression counts matched production; 9100 deleted. Scheduled: VM 101 backed up daily at 04:00 and 22:30
      (`pve-prod01` is on Denver time); prune daily at midnight, keep last 14 (7 days at two a day); garbage collection
      Saturdays 18:15. The 04:00 backup falls inside the nightly timers' window (10:00 to 12:30 UTC), which is safe
      (Postgres keeps each transaction whole) but may leave that morning's data out.

## 11. Before the first real send

Every one of these, not most:

- [ ] Item 183: the production Constant Contact account, its behaviours checked.
- [ ] Item 189: the eligibility gate, built 2026-11-04; the final permission lists loaded here (§7).
- [ ] Item 96: the sending domain decided and set up.
- [ ] Item 135: the copy for this send, supplied by marketing (each send, not a one-time sign-off; developer, 2026-11-04).
- [x] Item 285: the suppression list copied, counts matched.
- [x] Items 232, 233: a completed backup and a tested restore (2026-11-06, §10).
- [x] Item 236: host alerts arrive (2026-11-09).

## 12. Updating the server later

- [ ] On `hail-dev`, before anything else: review every file in `docs/` and bring it up to date, as in §0 (items 212,
      298); commit the changes, so the release carries them.
- [ ] On `hail-dev`: add the release's section to `CHANGELOG.md`, commit and push, then `scripts/release.sh vX.Y.Z`
      (`--check` first to see what it would tag). PATCH for fixes only, MINOR for anything people will notice, MAJOR
      for a milestone (item 289).
- [ ] Here: `git fetch --tags`, `git checkout vX.Y.Z`, `scripts/write_version.sh` (if GitHub refuses the tag, push it
      from `hail-dev` as in §3, item 306). To roll back, check out the previous
      tag the same way (a migration that has run is not undone by a checkout: read `sql/` first).
- [ ] `docker compose run --rm -T loader bash scripts/migrate.sh`, read what is pending, then `--apply`. A migration is
      never edited once applied.
- [ ] Rebuild `ingest` after a change to anything it imports (`scripts/status.sh images` names the files), and `web` or
      `app` after a change to `requirements.txt` or `docker/app.Dockerfile`.
- [ ] `scripts/status.sh images`: all OK.
- [ ] **If `web` was rebuilt, `docker compose up -d --force-recreate web`** (item 302): `restart` keeps the existing
      container, and with it the image it was created from, so a rebuilt `web` never goes live. Plain `up -d` is not
      enough either: on 2026-11-04 it printed `Running` after a rebuild and left `web` on the old image. Check with
      `scripts/status.sh images`: it prints `OLD IMAGE` under a service whose running container is not on the tagged
      image. By hand, `docker inspect $(docker compose ps -q web) --format '{{.Image}}'` against
      `docker image inspect hail-system-web --format '{{.Id}}'`: the two must match.
- [ ] Otherwise `docker compose restart web`, every time: it reads the version once, at startup (item 289), and Python only
      reloads on a restart (templates reload on their own, because `create_app()` sets `TEMPLATES_AUTO_RELOAD`, item
      290). Check the footer shows the new release with no `-dirty` and no `-N-g...` part, and update "Release
      running" at the top of this file.
