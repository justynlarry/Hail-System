# Rocky Linux Server Setup

## Production hosts (2026-10-20)
- `pve-prod01.rbi.internal`, 192.168.16.20: HP ProDesk 400 G6, Proxmox VE. Runs the production VM
  `rbi-hail01.rbi.internal` (VM 101, Rocky Linux 10.2, 8 cores, 28 GB, 400 GB), created but not started.
- `pbs-prod01.rbi.internal`, 192.168.16.21: Dell OptiPlex 3020, Proxmox Backup Server.
- Both passed `scripts/hw-acceptance-test.sh` on 2026-10-19. Details: `docs/hail-consolidated.md` §9.
- The steps below build the Rocky VM (`rbi-hail01`), not the Proxmox hosts.
- The two hosts are reached over a separate RBI Tailscale tailnet, shared into the developer's personal one,
  with OpenSSH and keys (decision log 2026-10-20). After the OS install, in the guest: install
  `qemu-guest-agent` and enable `fstrim.timer` (parking-lot item 233).

## Hostname
Normally set during the Rocky installer — but if it was skipped, the box comes
up as `localhost.localdomain`:
```
hostnamectl set-hostname <server-name>
```

## Network:
**Set up the Network Connection by finding the NIC name, set the IP Address and Gateway**

```
nmcli connection show

nmcli connection modify "<connection-name>" \
  ipv4.method manual \
  ipv4.addresses <ip_address>/<subnet> \
  ipv4.gateway <gateway> \
  ipv4.dns 1.1.1.1 \
  ipv4.ignore-auto-dns yes

nmcli connection up "<connection-name>"

ping google.com

```
Notes:
- `modify` takes the **connection NAME** from the first column, not the DEVICE
  name. They are often similar enough to hide the mistake.
- `ipv4.method manual` is required. Setting an address while the connection is
  still `auto` leaves DHCP in charge and the static config is silently ignored.
  Same for DNS — without `ignore-auto-dns`, DHCP's servers win.
- `connection up` re-applies the profile. If you are on SSH over this same
  link, expect to be dropped and reconnect on the new address.

## Update the OS
`sudo dnf update -y`

Reboot if the kernel was updated.

## Configure Rocky
**Remove Podman**
`sudo dnf remove -y podman buildah`

**Install Docker from CentOS:**
```
sudo dnf install -y dnf-plugins-core

sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo systemctl enable --now docker

sudo usermod -aG docker $USER

newgrp docker

docker run --rm hello-world
```

`newgrp docker` only applies to the shell you run it in. Log out and back in
(or reboot) to pick the group up everywhere.

**Set timezone to UTC - The system timestamps are all in UTC, part of the system's job is to convert to America/Denver**
```
sudo timedatectl set-timezone UTC
```

**Set journald to Persistent -> otherwise logs will all be erased on reboot**
```
sudo mkdir -p /var/log/journal
```

In `/etc/systemd/journald.conf`, under the `[Journal]` section, set
`SystemMaxUse=500M` to prevent `journald` from filling the disk. The stock line
is present but commented out — edit it in place. A key appended below the
`[Journal]` section is ignored.

Then restart, so both the persistent directory and the size cap take effect:
```
sudo systemctl restart systemd-journald

sudo systemctl status systemd-journald
```

## Firewall
Rocky enables `firewalld` by default. Left closed — nothing needs an inbound
port opened on the LAN or WAN.

**Phase 2:** the web container binds to `127.0.0.1:8000` only, and
`tailscale serve --bg 8000` (see "Install Tailscale" below) fronts it over the
tailnet instead of publishing a port anywhere firewalld would see it. The
earlier plan was a Cloudflare tunnel; Phase 2 uses Tailscale instead because
the entire user base during Phase 2 is one person already on the tailnet, and
it costs nothing to set up (decision-log 2026-09-14). **This is an interim
arrangement, not the final one** — revisit at Phase 6, when staff need access:
Cloudflare Access is less client-side setup for them (a browser and an email
code, nothing installed) and does not depend on RBI controlling DNS, which
reverses the assumption that the tunnel is the heavier option.

Left as-is deliberately regardless of which one fronts it. Note that Docker
writes iptables rules directly and can publish a container port past
firewalld's zones — so a `-p` in a compose file is not covered by the
assumption above, which is also why the web container binds to loopback
rather than a wildcard address.

## SELinux Configuration
*Should be okay out of the box, but Docker will need to be configured correctly:*
- In docker-compose.yml:
```
volumes:
  - ./sql:/sql:ro,Z
```

- If any problems arise, remember: `ausearch -m avc -ts recent`

## Database Migrations

Apply the migrations with `scripts/migrate.sh` (item 277), in the `loader` service, which connects as
`hail_admin` and sees the repo at `/repo`. On a new server the database is empty, so one run builds the whole
schema in number order and records every file in `schema_migrations`:
```
docker compose run --rm -T loader bash scripts/migrate.sh             # report first: every file pending, nothing changed
docker compose run --rm -T loader bash scripts/migrate.sh --apply     # apply them all, in order, recording each
docker compose run --rm -T loader bash scripts/migrate.sh             # expect "recorded N, pending 0"
```
It applies only files named `NNN_name.sql`. **The other files in `sql/` are not migrations, and are never applied
on this server:** `guard_test.sql` (a test that rolls itself back) and the dev seeds `dev_rehearsal_seed*.sql`,
which insert the developer's `+t` test realtors with fake listings and matches and are headed "Never run on
production". Each file runs with `ON_ERROR_STOP`, and the first failure stops the run with nothing after it applied.

Later migrations go the same way on each machine (`hail-dev` and production): the report, read what is pending,
then `--apply`. A migration is never edited once applied; add a new one (`docs/command-ref.md`, Migrations).

**Check the result against a fresh build, not with `guard_test.sql` here.** `guard_test.sql` needs existing rows
(users, realtors, matches, a suppression) and stops at its first insert on an empty database, and the rows it would
need are what must not go on production. Build the same files in a throwaway container, run `guard_test.sql` there,
and diff that schema with this one (`docs/schema-review.md` §1). They should be identical.

**Do this before the first RentCast pull, not after.** Several later
migrations (`024`, `025`, `027` — see parking-lot item 55 and
`docs/decision-log.md`, "`address_key`: coalescing every field, not just
the directional") rebuild a `GENERATED` column and depend on that column
computing correctly from the first row ever inserted. Running them in
order before any real data exists means every row is always generated by
the final, correct version of the function — never by an intermediate,
already-superseded one. Run them out of order, skip one, or start pulling
mid-sequence, and this class of problem can resurface: `migrate.sh` applies in
number order and refuses a database that has tables but nothing recorded, so a
mid-sequence start is caught.

## Data on a new server (decided 2026-11-02)

Production starts from code and public sources, not from a copy of `hail-dev`. The one exception is the suppression
list. In order:

1. **Schema:** `migrate.sh --apply` (above). It also creates the `system` account, the `settings` row with its
   defaults, radar parameter set 1, and likelihood set 1 with its 20 rules.
2. **The first admin**, with the bootstrap script, as `hail_admin`:
   ```
   PW=$(docker compose run --rm --no-deps -T --entrypoint sh loader -c 'printf %s "$PGPASSWORD"')
   docker compose run --rm --no-deps -e PGUSER=hail_admin -e PGPASSWORD="$PW" web \
       python3 scripts/create_user.py --user-name <name> --first-name <first> --last-name <last> --email <email> --role admin
   unset PW
   ```
   The first line takes the password as Compose resolves it (the `loader` service's `PGPASSWORD` is
   `POSTGIS_ROOT_PASSWORD`), not with `grep | cut`, which passes a `$$` in `.env` as written and fails to sign in (item
   304). The single quotes make `$PGPASSWORD` expand inside the container. That line keeps `-T` because it captures
   output; the second has **no `-T`**, because `create_user.py` asks for the new password with `getpass`, which cannot
   hide what is typed without a terminal. Every other user is added on the admin page.
3. **Reference data.** Copy the two TIGER folders across first (below, "to move files untracked in git"), then:
   ```
   docker compose run --rm -T loader bash scripts/load_reference.sh    # report types, sources, ZCTAs, counties
   docker compose run --rm -T loader bash scripts/load_coverage.sh     # config/coverage_zips.txt, 183 zips
   ```
   `scripts/load_municipal.sh` is for the parked permits work and can wait.
4. **Settings, on the admin page,** so each one lands in `settings_history`. `hail-dev`'s values on 2026-11-02: zip and
   match radius 5.0, RentCast billing day 9, quota 1,000, listing freshness 7 days, match age 30 days, email cap 14
   days. Also the email images, sender and contact settings, and `storm_states`.
5. **Suppressions: copy `dnc_list` from `hail-dev`,** 768 active and 1 removed (item 285). Do this before any send.
   The file holds real addresses, so keep it under `data/`, and delete it on both machines once the load has
   reported:
   ```
   bash scripts/dnc_copy.sh export data/dnc_list.csv        # on hail-dev
   scp data/dnc_list.csv hail-user@100.74.188.103:hail-system/data/   # over Tailscale, from hail-dev
   bash scripts/dnc_copy.sh load data/dnc_list.csv          # on rbi-hail01
   rm data/dnc_list.csv                                     # on both machines
   ```
   The load is one transaction. It refuses, with nothing committed, if an address is missing afterwards, or if an
   address active in the file is removed on the target. Its last table must show the same active and removed counts
   as `hail-dev`.
6. **Public data, rebuilt from source.** Build the `ingest` image first (`docker compose build ingest`):
   ```
   docker compose run --rm ingest python3 scripts/iem_backfill.py --start 2004-01-01 --end <today>
   docker compose run -d --rm ingest python3 scripts/radar_ingest.py backfill        # about 2½ hours on rbi-hail01
   docker compose run --rm ingest python3 scripts/radar_ingest.py warnings          # NWS warnings before the radar
   docker compose run --rm ingest python3 scripts/cocorahs_ingest.py backfill
   docker compose run --rm ingest python3 scripts/likelihood_ingest.py backfill     # after radar and CoCoRaHS
   ```
   The radar backfill loads NWS warnings only for the radar years (2020-10-14 on); `warnings` loads them back to
   the first storm report, so the Territory map and findings panel show warnings on the older days (item 307). It
   needs the IEM archive first, since it starts from the first stored report. On `hail-dev` the IEM archive took
   about ten runs of up to 2½ minutes each. `scripts/status.sh` shows each job's state.
7. **Timers:** `sudo cp systemd/* /etc/systemd/system/`, `sudo systemctl daemon-reload`, then
   `sudo systemctl enable --now` on the eight `.timer` units. The units name the user `hail-user` and
   `/home/hail-user/hail-system`, the same as on `hail-dev`.
8. **RentCast starts empty.** The first pull is a person's click, as always. `hail-dev`'s listings are mostly past the
   7-day freshness window, and its matches point at IEM ids the rebuild changes. **Quota:** both machines use one
   RentCast account, so calls made on `hail-dev` in the current period don't appear in production's usage count for
   its first period. Accepted 2026-11-02, with the developer as the only user and tracking in beta.

**Never copied from `hail-dev`:**
- Constant Contact grants (`oauth_tokens`) and the `cc_*` tables: production has its own account (item 183) and token
  key.
- Rehearsal sends: `sent_emails`, `send_log`, `email_test_sends` and the drip tables.
- Email templates: the real ones wait on item 135.
- Run logs, `schema_migrations`, `geocode_cache` and `address_searches`.

## Git/Github Setup
```
sudo dnf install -y git

git config --global user.name "<your-name>"

git config --global user.email "<your-email>"

git config --global init.defaultBranch main

ssh-keygen -t ed25519 -C "<server-name>"

cat ~/.ssh/id_ed25519.pub
```

Paste that public key into GitHub → Settings → SSH and GPG keys. Then `ssh -T git@github.com` to confirm
— it will say you've authenticated but shell access is denied, which is success.

**Clone the repo:**
```
git clone git@github.com:justynlarry/Hail-System.git ~/<repo-dir>

```
Pick `<repo-dir>` once and use the same spelling in the rsync step below.
Linux is case-sensitive: cloning to `Hail-System` and rsyncing to `hail-system`
gives you two sibling directories, a repo with no data and data with no repo.

## Install Tailscale:
```
curl -fsSL https://tailscale.com/install.sh | sh

sudo tailscale up
```
The install script only installs the daemon. `tailscale up` is what authenticates
the node and joins it to the tailnet — it prints a URL to open in a browser.

**Two uses on this box.** File movement between machines (below), and, from
Phase 2 on, fronting the web UI: `tailscale serve --bg 8000` against the
loopback-bound container gives `https://<hostname>.<tailnet>.ts.net` with a
Tailscale-issued certificate — see "Firewall" above.

### *Not necessary on EVERY server, but to move files untracked in git*

**Run these from the SOURCE machine, not the box you are building.** If the
destination is a tailnet name, it has to come after `tailscale up` on both ends.

Only the two extracted TIGER folders go across (item 304): they are all
`scripts/load_reference.sh` reads from outside the clone (lines 35-36).

```
# dry run first: lists what would transfer, moves nothing (14 files, 7 per folder)
rsync -avn --relative ~/<repo-dir>/./data/raw/tiger/tl_2025_us_zcta520 \
                      ~/<repo-dir>/./data/raw/tiger/tl_2025_us_county \
      <user>@<target-host>:~/<repo-dir>/

# then for real
rsync -avP --relative ~/<repo-dir>/./data/raw/tiger/tl_2025_us_zcta520 \
                      ~/<repo-dir>/./data/raw/tiger/tl_2025_us_county \
      <user>@<target-host>:~/<repo-dir>/
```

`--relative` with the `/./` keeps the path after it, so the files land at
`~/<repo-dir>/data/raw/tiger/...` on the target. Run the dry run again afterwards:
an empty file list means the two sides match (959,120,337 bytes on 2026-11-05).
For `rbi-hail01`, `<target-host>` is its Tailscale IP, `100.74.188.103`: it is
on a different Tailscale login and its short name does not resolve from `hail-dev`.

**What stays behind, and why:**
- `reference/`: three real suppression exports (Airtable and Constant Contact)
  that nothing on a server reads, plus files `build_reference_tables.py`
  regenerates. The suppression list moves only by `dnc_copy.sh` (above).
- The rest of `data/`: the IEM archive CSVs (the backfill downloads from IEM),
  the TIGER zips (re-downloadable from the Census Bureau), and the parked
  permits research, some of it possibly homeowner records.
- `.env` and `cloudflared/`: every server has its own secrets and tunnel.
- `output/`, `scratchpad/`, `claude-resume.txt`: working files.

Note that `planning/report_types.csv` arrives via the clone, not the rsync. It
is tracked, so if it has not been committed and pushed the loader will have no
seed.
