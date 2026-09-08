# Rocky Linux Server Setup

## Network:
**Set up the Network Connection by finding the NIC name, set the IP Address and Gateway**

```
nmcli connection show

nmcli connection modify <device> ipv4.adresses <ip_address>/<subnet> ipv4.gateway <gateway> ipv4.dns 1.1.1.1

sudo systemctl restart NetworkManager

ping google.com

```

## Update the OS
`sudo dnf update -y`

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

**Set timezone to UTC - The system timestamps are all in UTC, part of the system's job is to convert to America/Denver**
```
sudo timedatectl set-timezone UTC
```

**Set journald to Persistent -> otherwise logs will all be erased on reboot**
```
sudo mkdir -p /var/log/journal

sudo systemctl restart systemd-journald

sudo systemctl status systemd-journald
```

**In `/etc/systemd/journald.conf` add `SystemMaxUse=500M` to prevent `journald` from filling the disk**

## SELinux Configuration
*Should be okay out of the box, but Docker will need to be configured correctly:*
- In docker-compose.yml:
```
volumes:
  - ./spl:/sql:ro,Z
```

- If any problems arise, remember: `ausearch -m avc -ts recent`

## Git/Github Setup
```
sudo dnf install -y git

git config --global user.name "justynlarry"

git config --global user.email "justynlarry@gmail.com"

git config --global init.defaultBranch main

ssh-keygen -t ed25519 -C "<server-name>"

cat ~/.ssh/id_ed25519.pub
```

Paste that public key into GitHub → Settings → SSH and GPG keys. Then `ssh -T git@github.com` to confirm
— it will say you've authenticated but shell access is denied, which is success.

