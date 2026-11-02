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

Update this table as the build moves. Item numbers are in `docs/parking-lot.md`.

| Step | State (2026-11-02) |
|---|---|
| 0. Before you start | Production Constant Contact account (183), sending domain (96) and alert SMTP account (236) not yet set up |
| 1. Hosts | `pve-prod01`, `pbs-prod01` built and acceptance-tested 2026-10-19; hardening (237), notifications (236) and retention (233) open |
| 2. OS | VM 101 created, Rocky 10.2 ISO attached, **not started** |
| 3 to 9 | Not started |
| 10. Checks | Not started |
| 11. Before the first real send | Items 183, 189, 96, 135, 285, 232/233, 236 open |

---

## 0. Before you start

Have these in hand, or know where they come from:

- [ ] **Name and address:** hostname, static IP, gateway, DNS. For `rbi-hail01`: `docs/server-setup.md`, Production
      hosts, and `docs/hail-consolidated.md` §9.
- [ ] **Access:** a Tailscale account to join the server to, and your SSH key.
- [ ] **RentCast key:** the same RentCast account as `hail-dev` (decided 2026-11-02; the first billing period's usage
      count on production misses `hail-dev`'s calls, accepted).
- [ ] **Constant Contact:** the production account's API key and secret (item 183), and the redirect URI on the
      production hostname, registered in that account's app settings.
- [ ] **Cloudflare:** a tunnel and hostname for this server, separate from `hail-dev`'s (decision log, "Tunnel
      topology", item 110): `app.roofbrokersinc-weather.com` for production.
- [ ] **Alerts:** an SMTP account for host notifications (item 236; a dedicated Gmail account with an app password
      was proposed).

## 1. The hosts (when the server is a Proxmox VM)

Done on the Proxmox and backup hosts, before the VM is started or joins Tailscale (item 237).

- [ ] Tailscale key expiry off on both hosts; Tailscale SSH off on both.
- [ ] Remove any stale node with the VM's name from the tailnet it will join (a MagicDNS name collision otherwise).
- [ ] sshd: keys only, no root password (`PermitRootLogin prohibit-password`, `PasswordAuthentication no`,
      `KbdInteractiveAuthentication no` in a drop-in under `/etc/ssh/sshd_config.d/`). Prove key login works first,
      keep a session open, `sshd -t` before reloading.
- [ ] A notification target that delivers, and a test notice that arrives (item 236).
- [ ] The backup job for the VM, with retention, prune and garbage collection (item 233).
- [ ] The VM: CPU type `x86-64-v3` (Rocky 10 needs it), disk with `discard=on` and `ssd=1`, `agent=1`.

## 2. The operating system

Detail: `docs/server-setup.md`, from "Hostname" to "SELinux Configuration".

- [ ] Install Rocky Linux; set the hostname.
- [ ] Static IP with `nmcli` (the connection NAME, `ipv4.method manual`, `ignore-auto-dns`).
- [ ] `sudo dnf update -y`, reboot if the kernel changed.
- [ ] `qemu-guest-agent` installed and running; `fstrim.timer` enabled (item 233).
- [ ] Timezone UTC; journald persistent with `SystemMaxUse=500M`.
- [ ] firewalld left on with nothing opened.
- [ ] Podman removed, Docker CE installed from Docker's repository, `hello-world` runs.
- [ ] **The account is `hail-user`, UID 1000, and the repo will be `~/hail-system`.** The systemd units name the
      user and the path (`systemd/*.service`), and the `cloudflared` service runs as `1000:1000` to read its credential.
      The first account made in the Rocky installer gets UID 1000; check with `id hail-user`.
- [ ] Tailscale installed and joined (`sudo tailscale up`).

## 3. Code and files

Detail: `docs/server-setup.md`, "Git/Github Setup" and "to move files untracked in git".

- [ ] git installed, an SSH key for this server added to GitHub, `ssh -T git@github.com` answers.
- [ ] `git clone git@github.com:justynlarry/Hail-System.git ~/hail-system`.
- [ ] From `hail-dev`, rsync `data/` and `reference/` into `~/hail-system/` (dry run first; trailing slashes matter).
      These hold the TIGER shapefiles the reference load needs.

## 4. Secrets

- [ ] `cp .env.example .env`, `chmod 600 .env`, and fill in **new** values. Generate them on the server:
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
- [ ] Use valid shell names only (item 242): `. ./.env` must not fail.

## 5. Build and start

- [ ] `docker compose build web app ingest loader`
- [ ] `docker compose up -d postgis web` and wait for `postgis` to report `healthy` (`docker compose ps`).
- [ ] `scripts/status.sh images` reads OK for all four.

The `cloudflared` service waits for step 8.

## 6. The database

Detail: `docs/server-setup.md`, "Database Migrations".

- [ ] `docker compose run --rm -T loader bash scripts/migrate.sh` (report: every file pending), then `--apply`, then the
      report again: `recorded N, pending 0`.
- [ ] Build the same files in a throwaway `postgis/postgis` container of the same tag, and diff its schema with this
      one: they should be identical (`docs/schema-review.md` §1). Not `guard_test.sql` here: it needs rows that must
      not go on production.

## 7. Data

Detail: `docs/server-setup.md`, "Data on a new server" (decided 2026-11-02). In order:

- [ ] The first admin with `scripts/create_user.py`, as `hail_admin`. Every other user on the admin page.
- [ ] `scripts/load_reference.sh`, then `scripts/load_coverage.sh` (the loader service).
- [ ] Settings on the admin page, so each lands in `settings_history`: radii, billing day, quota, freshness, match age,
      email cap, the email images, sender and contact settings, `storm_states`.
- [ ] **Suppressions:** `scripts/dnc_copy.sh export` on `hail-dev`, copy the file over Tailscale, `scripts/dnc_copy.sh
      load` here, delete the file on both machines. The load's last table must show the same active and removed counts
      as `hail-dev` (item 285; close it then).
- [ ] The public data rebuilt by backfill: IEM from 2004, radar (about 3.6 hours), CoCoRaHS, then likelihood.
- [ ] The timers: `sudo cp systemd/* /etc/systemd/system/`, `sudo systemctl daemon-reload`,
      `sudo systemctl enable --now` on the eight `.timer` units.
- [ ] RentCast data starts empty: the first pull is a person's click.

## 8. Access

- [ ] **For the developer:** `tailscale serve --bg 8000`; the app answers at `https://<hostname>.<tailnet>.ts.net`.
- [ ] **For staff:** this server's own Cloudflare tunnel, never `hail-dev`'s (two connectors on one tunnel are
      load-balanced replicas). On a machine logged in to the Cloudflare account, `cloudflared tunnel create <name>`
      writes the credential `<tunnel-id>.json`; route the production hostname to that tunnel. Here:
      `cp cloudflared/config.example.yml cloudflared/config.yml`, fill in the tunnel ID and hostname, put the
      credential beside it (mode 600, owned by `hail-user`). Both files stay out of git (item 287). Add the hostname to
      a Cloudflare Access policy (allowed emails, one-time PIN), then `docker compose up -d cloudflared` and look for
      `Registered tunnel connection` in its log.
- [ ] `web` stays bound to `127.0.0.1:8000`; nothing is published on the LAN.

## 9. Constant Contact

- [ ] At `/cc`, connect the production account (the OAuth sign-in returns to `CC_REDIRECT_URI`).
- [ ] Run the first unsubscribe sync from `/cc`; it must complete before any send.
- [ ] The behaviours item 183 lists, checked on this account.

## 10. Checks

- [ ] `scripts/status.sh`: each nightly job has run, after the first night.
- [ ] `scripts/status.sh images`: all OK.
- [ ] Sign in as an admin, a sender and a viewer: each opens what its role may (Storm Days, Territory with the map,
      the match page, `/admin` for the admin only) and is refused the rest.
- [ ] A backup of the VM has completed, and a test restore of it has been booted with the network disconnected
      (item 233; the restore target must support `x86-64-v3`).

## 11. Before the first real send

Every one of these, not most:

- [ ] Item 183: the production Constant Contact account, its behaviours checked.
- [ ] Item 189: the eligibility gate.
- [ ] Item 96: the sending domain decided and set up.
- [ ] Item 135: the email copy signed off.
- [ ] Item 285: the suppression list copied, counts matched.
- [ ] Items 232, 233: a completed backup and a tested restore.
- [ ] Item 236: host alerts arrive.

## 12. Updating the server later

- [ ] `git pull`.
- [ ] `docker compose run --rm -T loader bash scripts/migrate.sh`, read what is pending, then `--apply`. A migration is
      never edited once applied.
- [ ] Rebuild `ingest` after a change to anything it imports (`scripts/status.sh images` names the files), and `web` or
      `app` after a change to `requirements.txt` or `docker/app.Dockerfile`.
- [ ] `docker compose restart web` after a Python change; templates reload on their own.
- [ ] `scripts/status.sh images`: all OK.
