# Rocky Linux Server Setup

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
Rocky enables `firewalld` by default. The web UI is reached through a Cloudflare
tunnel, which is outbound-only, so no inbound ports should need opening.

Left as-is deliberately. Note that Docker writes iptables rules directly and can
publish a container port past firewalld's zones — so a `-p` in a compose file is
not covered by the assumption above.

## SELinux Configuration
*Should be okay out of the box, but Docker will need to be configured correctly:*
- In docker-compose.yml:
```
volumes:
  - ./sql:/sql:ro,Z
```

- If any problems arise, remember: `ausearch -m avc -ts recent`

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

### *Not necessary on EVERY server, but to move files untracked in git*

**Run these from the SOURCE machine, not the box you are building.** If the
destination is a tailnet name, it has to come after `tailscale up` on both ends.

```
# dry run first — shows what would transfer, moves nothing
rsync -avn --exclude='__pycache__/' --exclude='*.pyc' \
  ~/<repo-dir>/data ~/<repo-dir>/reference \
  <user>@<target-host>:~/<repo-dir>/

# then for real
rsync -avP --exclude='__pycache__/' --exclude='*.pyc' \
  ~/<repo-dir>/data ~/<repo-dir>/reference \
  <user>@<target-host>:~/<repo-dir>/
```

Trailing slashes matter. Bare `~/<repo-dir>/data` copies the directory itself;
`data/` with a slash copies its *contents* loose into the destination.

`data/` and `reference/` are the only gitignored paths that carry anything —
`scripts/__pycache__/` is bytecode and is excluded on purpose.

Note that `planning/report_types.csv` arrives via the clone, not the rsync. It
is tracked, so if it has not been committed and pushed the loader will have no
seed.
