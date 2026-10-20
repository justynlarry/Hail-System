#!/usr/bin/env bash
# =============================================================================
# hw-acceptance-test.sh — Hardware acceptance test for server-bound machines
#
# Built for:  HP ProDesk 400 G6 (i5, 32 GB, 1 TB NVMe)  — rbi-hail server
#             Dell OptiPlex 3020 (i5, 8 GB, 500 GB HDD)
# Works on:   Proxmox VE / Proxmox Backup Server hosts (run on the HOST, not
#             in a VM), plus plain Debian/Ubuntu (apt) and Rocky/Alma (dnf).
#             Proxmox-aware: follows vmbr bridges to the real NIC, skips ZFS
#             zvols, caps the ZFS ARC during the RAM test, warns on running
#             guests. Nothing here is destructive: disk benchmarks are
#             READ-ONLY against the raw device.
#
# What it checks:
#   1. Inventory   — model, BIOS, CPU, DIMMs per slot, VT-x, UEFI, RTC drift
#   2. Pre-flight  — hardware errors already sitting in the kernel log
#   3. Disks       — SMART/NVMe health, wear, link width/speed, read benchmark,
#                    SMART self-test (started here, graded at the end)
#   4. Memory      — memtester, run in parallel across cores (much faster
#                    than one big instance)
#   5. CPU         — thermal soak + combined CPU/RAM load (PSU/VRM stress),
#                    with --verify so wrong answers are caught, not just heat
#   6. Network     — link speed/duplex, error counters, optional iperf3
#   7. Post-flight — new kernel errors under load, self-test results, summary
#
# Usage:
#   sudo ./hw-acceptance-test.sh                       # standard (~45–75 min)
#   sudo ./hw-acceptance-test.sh --quick               # smoke test (~15 min)
#   sudo ./hw-acceptance-test.sh --iperf 192.168.1.50  # add throughput test
#   sudo ./hw-acceptance-test.sh --help
#
# Run it inside tmux — if your SSH session drops, the test keeps going:
#   sudo dnf install -y tmux   # or apt install tmux
#   tmux new -s hwtest         # detach: Ctrl-b then d | reattach: tmux attach -t hwtest
#
# Exit codes: 0 = all PASS, 1 = warnings, 2 = failures
#
# Note: in-OS RAM testing is good, not perfect — the kernel and running
# services occupy memory memtester can't touch. For a production box, an
# overnight memtest86+ run from USB is still the gold standard.
# =============================================================================

# Deliberately NOT using `set -e`. In a diagnostic script, a failing command
# is often the *finding* — we want to record it and keep testing, not abort.
# -u (error on unset variables) and pipefail still catch our own bugs.
set -uo pipefail

# =============================================================================
# Configuration (override with flags — see --help)
# =============================================================================
CPU_MINUTES=15          # total CPU stress time: 2/3 thermal soak, 1/3 combined
MEM_PCT=85              # % of MemAvailable to test with memtester
MEM_PASSES=1            # memtester passes (each pass = ~16 pattern tests)
FIO_SECONDS=30          # per read benchmark, per disk
IPERF_SERVER=""         # run `iperf3 -s` on another box and pass its IP
SMART_TEST="short"      # short (~2 min) or long (HDD: hours — graded later)
ASSUME_YES=0

# Logs go to /var/log, NOT /tmp: on many distros /tmp is tmpfs (RAM-backed),
# and we're about to fill RAM on purpose.
LOG_ROOT="/var/log/hw-acceptance"

# Thresholds
TEMP_WARN_C=90          # Intel desktop parts throttle near 100°C
TEMP_FAIL_C=98
HDD_SEQ_WARN_MBS=80     # healthy 7200rpm 3.5" HDD: ~120–200 MB/s outer tracks
SSD_SEQ_WARN_MBS=400    # SATA SSD: ~500+ MB/s
NVME_SEQ_WARN_MBS=800   # even budget PCIe 3.0 NVMe: 1500+ MB/s
HDD_HOURS_WARN=35000    # ~4 years powered on
RTC_DRIFT_WARN_S=120

# =============================================================================
# Argument parsing
# =============================================================================
usage() {
    cat <<EOF
Usage: sudo $0 [options]

  --quick              Smoke test: 5 min CPU, 25% RAM, 10 s disk benchmarks
  --cpu-minutes N      CPU stress duration in minutes (default: $CPU_MINUTES)
  --mem-percent N      Percent of available RAM to test (default: $MEM_PCT)
  --mem-passes N       memtester passes (default: $MEM_PASSES)
  --iperf HOST         Run iperf3 against HOST (start 'iperf3 -s' there first)
  --long-smart         Run SMART long self-test instead of short
  -y, --yes            Don't prompt before starting
  -h, --help           Show this help
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --quick)        CPU_MINUTES=5; MEM_PCT=25; FIO_SECONDS=10 ;;
            --cpu-minutes)  CPU_MINUTES="$2"; shift ;;
            --mem-percent)  MEM_PCT="$2"; shift ;;
            --mem-passes)   MEM_PASSES="$2"; shift ;;
            --iperf)        IPERF_SERVER="$2"; shift ;;
            --long-smart)   SMART_TEST="long" ;;
            -y|--yes)       ASSUME_YES=1 ;;
            -h|--help)      usage; exit 0 ;;
            *) echo "Unknown option: $1" >&2; usage; exit 64 ;;
        esac
        shift
    done
}

# =============================================================================
# Logging helpers
# =============================================================================
START_EPOCH=$(date +%s)
HOST_SHORT=$(hostname -s 2>/dev/null || hostname)
RUN_DIR="${LOG_ROOT}/${HOST_SHORT}-$(date +%Y%m%d-%H%M%S)"
LOG="${RUN_DIR}/test.log"

PASS_N=0
WARNINGS=()
FAILURES=()
SELFTEST_DEVS=()
declare -A SELFTEST_METHOD=()

# Only emit color codes when stdout is a terminal, so redirected output
# (e.g. `... > out.txt`) stays clean. The log file never gets colors.
if [[ -t 1 ]]; then
    C_G=$'\e[32m'; C_Y=$'\e[33m'; C_R=$'\e[31m'; C_C=$'\e[36m'; C_B=$'\e[34m'; C_N=$'\e[0m'
else
    C_G=""; C_Y=""; C_R=""; C_C=""; C_B=""; C_N=""
fi

_log() {   # _log LEVEL COLOR MESSAGE
    local ts; ts=$(date +%H:%M:%S)
    printf '[%s] %-5s %s\n' "$ts" "$1" "$3" >> "$LOG"
    printf '[%s] %s%-5s%s %s\n' "$ts" "$2" "$1" "$C_N" "$3"
}
info() { _log INFO "$C_B" "$*"; }
pass() { _log PASS "$C_G" "$*"; PASS_N=$((PASS_N + 1)); }
warn() { _log WARN "$C_Y" "$*"; WARNINGS+=("$*"); }
fail() { _log FAIL "$C_R" "$*"; FAILURES+=("$*"); }

section() {
    printf '\n%s== %s ==%s\n' "$C_C" "$1" "$C_N"
    printf '\n== %s ==\n' "$1" >> "$LOG"
}

# Run a command, showing output live AND appending it to the log, while
# preserving the command's own exit code. Without PIPESTATUS, `cmd | tee`
# returns tee's exit status (almost always 0) and hides failures.
run() {
    "$@" 2>&1 | tee -a "$LOG"
    return "${PIPESTATUS[0]}"
}

# ZFS ARC limit to restore after the memory test (empty = we didn't touch it)
ZFS_ARC_PARAM="/sys/module/zfs/parameters/zfs_arc_max"
ZFS_ARC_ORIG=""

restore_zfs_arc() {
    if [[ -n "$ZFS_ARC_ORIG" ]]; then
        echo "$ZFS_ARC_ORIG" > "$ZFS_ARC_PARAM" 2>/dev/null
        ZFS_ARC_ORIG=""
    fi
}

# Kill any background jobs (thermal monitor, memtesters) if we exit early,
# and put the ZFS cache limit back the way we found it.
cleanup() {
    local pids
    pids=$(jobs -pr)
    [[ -n "$pids" ]] && kill $pids 2>/dev/null
    restore_zfs_arc
    rm -f "${RUN_DIR}/.phase"
}
trap cleanup EXIT
trap 'echo; warn "Interrupted by user"; exit 130' INT TERM

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "${C_R}Run as root: sudo $0${C_N}" >&2
        exit 1
    fi
}

# Hardware tests only mean something on bare metal: inside a VM there's no
# SMART, no real temperature sensor, and "RAM" is whatever the host gave you.
check_environment() {
    local virt
    virt=$(systemd-detect-virt 2>/dev/null)
    if [[ -n "$virt" && "$virt" != "none" ]]; then
        echo "${C_R}This is running inside a VM/container ($virt).${C_N}"
        echo "Run it on the Proxmox host itself (node shell or SSH to the host)."
        exit 1
    fi

    if [[ -f /etc/pve/.version ]] || command -v pveversion >/dev/null 2>&1; then
        info "Proxmox VE host detected: $(pveversion 2>/dev/null | head -1)"
    elif command -v proxmox-backup-manager >/dev/null 2>&1; then
        info "Proxmox Backup Server detected: $(proxmox-backup-manager version 2>/dev/null | head -1)"
    fi

    # Guests compete with the test for CPU/RAM and skew results — and a
    # memory test that fills RAM can push running VMs into swap or the OOM killer.
    local running=0
    command -v qm  >/dev/null 2>&1 && running=$(( running + $(qm list  2>/dev/null | awk 'NR > 1 && $3 == "running"' | wc -l) ))
    command -v pct >/dev/null 2>&1 && running=$(( running + $(pct list 2>/dev/null | awk 'NR > 1 && $2 == "running"' | wc -l) ))
    if (( running > 0 )); then
        warn "$running VM/container(s) running — stop them first for valid results (qm shutdown <id> / pct shutdown <id>)"
        if (( ! ASSUME_YES )); then
            read -rp "Continue anyway? [y/N] " ans
            [[ $ans == [yY]* ]] || exit 1
        fi
    fi
}

# =============================================================================
# Sensor helpers
# =============================================================================

# Read CPU package temperature straight from sysfs — more reliable than
# parsing `sensors` output, whose format varies by version and chip.
pkg_temp() {
    local d f z
    for d in /sys/class/hwmon/hwmon*; do
        [[ "$(cat "$d/name" 2>/dev/null)" == "coretemp" ]] || continue
        for f in "$d"/temp*_label; do
            if grep -q "Package" "$f" 2>/dev/null; then
                echo $(( $(cat "${f%_label}_input") / 1000 ))
                return
            fi
        done
    done
    for z in /sys/class/thermal/thermal_zone*; do
        if [[ "$(cat "$z/type" 2>/dev/null)" == "x86_pkg_temp" ]]; then
            echo $(( $(cat "$z/temp") / 1000 ))
            return
        fi
    done
    echo ""
}

avg_mhz() {
    awk '/^cpu MHz/ {s += $4; n++} END {if (n) printf "%d", s / n}' /proc/cpuinfo
}

# Intel CPUs count every time they throttle for heat. A non-zero delta across
# the stress test is the clearest "cooling is inadequate" signal there is —
# better than guessing from frequency dips.
throttle_total() {
    local f sum=0
    for f in /sys/devices/system/cpu/cpu*/thermal_throttle/{core,package}_throttle_count; do
        [[ -r "$f" ]] && sum=$((sum + $(<"$f")))
    done
    echo "$sum"
}

# =============================================================================
# Kernel log helpers
# =============================================================================
HW_ERR_REGEX='mce:|machine check|hardware error|edac.*error|corrected error|uncorrect|i/o error|blk_update_request|critical medium error|ata[0-9.]+: (exception|failed command|hard resetting link|limiting sata link)|nvme[0-9]+.*(timeout|resetting controller|controller is down)|aer:.*error|pcie bus error|thermal.*critical'
HW_ERR_SEVERE='mce:|machine check|hardware error|edac.*error|uncorrect'

kernel_log() {   # kernel_log [since_epoch]
    if command -v journalctl >/dev/null 2>&1; then
        if [[ -n "${1:-}" ]]; then
            journalctl -k --no-pager -q --since "@$1" 2>/dev/null
        else
            journalctl -k -b --no-pager -q 2>/dev/null
        fi
    else
        dmesg 2>/dev/null
    fi
}

hw_errors() { kernel_log "${1:-}" | grep -Ei "$HW_ERR_REGEX" || true; }

# =============================================================================
# Dependencies
# =============================================================================
install_deps() {
    section "Installing dependencies"
    local pkgs=() pkg mgr

    if command -v dnf >/dev/null 2>&1; then
        mgr="dnf"
        # EPEL carries memtester and stress-ng; CRB holds some EPEL deps.
        rpm -q epel-release >/dev/null 2>&1 || dnf install -y -q epel-release >> "$LOG" 2>&1
        dnf install -y -q dnf-plugins-core >> "$LOG" 2>&1
        dnf config-manager --set-enabled crb >> "$LOG" 2>&1 || true
        pkgs=(stress-ng memtester smartmontools nvme-cli fio jq iperf3 ethtool
              dmidecode pciutils util-linux lm_sensors)
    elif command -v apt-get >/dev/null 2>&1; then
        mgr="apt"
        # On Proxmox without a subscription, the enterprise repo returns 401
        # and apt-get update exits non-zero. The Debian repos still refresh,
        # so installs work — but it's worth fixing before production.
        if ! apt-get update -qq >> "$LOG" 2>&1; then
            warn "apt-get update reported errors — on Proxmox without a subscription, disable the enterprise repo and enable no-subscription (see test.log)"
        fi
        pkgs=(stress-ng memtester smartmontools nvme-cli fio jq iperf3 ethtool
              dmidecode pciutils util-linux lm-sensors)
    else
        fail "No dnf or apt-get found — install dependencies manually"
        return
    fi

    # One package at a time, so one missing package doesn't sink the rest.
    for pkg in "${pkgs[@]}"; do
        if [[ $mgr == dnf ]]; then
            rpm -q "$pkg" >/dev/null 2>&1 && continue
            dnf install -y -q "$pkg" >> "$LOG" 2>&1 || info "Package unavailable: $pkg"
        else
            dpkg -s "$pkg" >/dev/null 2>&1 && continue
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$pkg" >> "$LOG" 2>&1 \
                || info "Package unavailable: $pkg"
        fi
    done

    local cmd missing=0
    for cmd in stress-ng smartctl jq dmidecode; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            fail "Required tool missing: $cmd"
            missing=1
        fi
    done
    (( missing )) && { echo "Cannot continue without required tools."; exit 2; }
    command -v memtester >/dev/null 2>&1 || info "memtester unavailable — will fall back to stress-ng memory verification"
    command -v fio       >/dev/null 2>&1 || info "fio unavailable — disk read benchmarks will be skipped"
    pass "Dependencies ready ($mgr)"
}

# =============================================================================
# 1. Inventory
# =============================================================================
system_inventory() {
    section "1/7 System inventory"

    info "System:  $(dmidecode -s system-manufacturer) $(dmidecode -s system-product-name)"
    info "Serial:  $(dmidecode -s system-serial-number)"
    info "BIOS:    $(dmidecode -s bios-version) ($(dmidecode -s bios-release-date))"
    info "OS:      $(. /etc/os-release && echo "$PRETTY_NAME") — kernel $(uname -r)"

    local cpu cores threads
    cpu=$(lscpu | awk -F: '/^Model name/ {sub(/^ +/, "", $2); print $2; exit}')
    cores=$(lscpu -p=CORE 2>/dev/null | grep -v '^#' | sort -u | wc -l)
    threads=$(nproc)
    info "CPU:     $cpu (${cores} cores / ${threads} threads)"

    [[ -d /sys/firmware/efi ]] && info "Boot:    UEFI" || info "Boot:    Legacy BIOS"

    # VT-x matters if this box will ever run VMs (Proxmox/KVM).
    if grep -qw vmx /proc/cpuinfo; then
        pass "VT-x virtualization enabled"
    else
        warn "VT-x not exposed — enable 'Virtualization Technology' in BIOS if this box will host VMs"
    fi

    # Memory: total and per-slot. `dmidecode -t 17` = SMBIOS "Memory Device".
    local total_gb
    total_gb=$(awk '/MemTotal/ {printf "%.1f", $2 / 1048576}' /proc/meminfo)
    info "RAM:     ${total_gb} GiB visible to the OS"

    local dimms populated=0 line
    dimms=$(dmidecode -t 17 | awk -F': ' '
        /^Memory Device/ { if (size != "") print loc " | " size " | " speed " | " mfr " | " part
                           size = loc = speed = mfr = part = "" }
        /^\tSize:/                                    { size = $2 }
        /^\tLocator:/                                 { loc = $2 }
        /^\tConfigured (Memory|Clock) Speed:/         { speed = $2 }
        /^\tManufacturer:/                            { mfr = $2 }
        /^\tPart Number:/                             { gsub(/ +$/, "", $2); part = $2 }
        END { if (size != "") print loc " | " size " | " speed " | " mfr " | " part }')
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        info "  slot:  $line"
        [[ "$line" != *"No Module Installed"* ]] && populated=$((populated + 1))
    done <<< "$dimms"
    if (( populated == 1 )); then
        info "One DIMM populated → single-channel memory (about half the bandwidth of two matched sticks)"
    fi

    # A dead CMOS battery shows up as RTC drift (and as the clock resetting
    # after power loss). Very common on 8–10 year old desktops.
    if command -v hwclock >/dev/null 2>&1; then
        local rtc sys drift
        rtc=$(date -d "$(hwclock --get 2>/dev/null)" +%s 2>/dev/null || echo "")
        sys=$(date +%s)
        if [[ -n "$rtc" ]]; then
            drift=$(( rtc > sys ? rtc - sys : sys - rtc ))
            if (( drift > RTC_DRIFT_WARN_S )); then
                warn "Hardware clock is ${drift}s off system time — CMOS battery may be weak"
            else
                pass "Hardware clock within ${drift}s of system time"
            fi
        fi
    fi

    # Full inventory snapshot for your records (handy for asset tracking).
    {
        echo "### dmidecode";  dmidecode
        echo "### lscpu";      lscpu
        echo "### lsblk";      lsblk -o NAME,SIZE,TYPE,TRAN,ROTA,MODEL,SERIAL
        echo "### lspci";      lspci -nn 2>/dev/null
        echo "### ip link";    ip -br link
    } > "${RUN_DIR}/inventory.txt" 2>&1
    info "Full inventory saved to ${RUN_DIR}/inventory.txt"
}

# =============================================================================
# 2. Pre-flight
# =============================================================================
test_preflight() {
    section "2/7 Pre-flight: existing hardware errors this boot"
    local errs
    errs=$(hw_errors)
    if [[ -z "$errs" ]]; then
        pass "No hardware errors in kernel log since boot"
    else
        warn "Kernel log already contains $(wc -l <<< "$errs") hardware-related line(s):"
        head -20 <<< "$errs" | while IFS= read -r l; do info "  $l"; done
        echo "$errs" > "${RUN_DIR}/preflight-kernel-errors.txt"
    fi
}

# =============================================================================
# 3. Disks
# =============================================================================
list_disks() {
    # -d: whole disks only, -n: no header, -p: full paths. Skip USB sticks
    # (often the installer media) and virtual devices. ZFS zvols (/dev/zdN —
    # VM disks on Proxmox) and Ceph RBDs report TYPE "disk" but aren't hardware.
    lsblk -dnpo NAME,TYPE,TRAN | awk '$2 == "disk" && $1 !~ /zram|loop|\/zd[0-9]|\/rbd/ && $3 != "usb" {print $1}'
}

ata_attr() {   # ata_attr JSON ID → raw value of SMART attribute ID
    jq -r --argjson id "$2" '[.ata_smart_attributes.table[]? | select(.id == $id) | .raw.value][0] // empty' <<< "$1"
}

check_nvme_health() {
    local dev=$1 json=$2 name ctrl h
    name=$(basename "$dev")
    h='.nvme_smart_health_information_log'

    local cw spare spare_th used media poh duw unsafe errlog
    cw=$(jq -r "$h.critical_warning // 0" <<< "$json")
    spare=$(jq -r "$h.available_spare // 100" <<< "$json")
    spare_th=$(jq -r "$h.available_spare_threshold // 10" <<< "$json")
    used=$(jq -r "$h.percentage_used // 0" <<< "$json")
    media=$(jq -r "$h.media_errors // 0" <<< "$json")
    poh=$(jq -r "$h.power_on_hours // 0" <<< "$json")
    duw=$(jq -r "$h.data_units_written // 0" <<< "$json")
    unsafe=$(jq -r "$h.unsafe_shutdowns // 0" <<< "$json")
    errlog=$(jq -r "$h.num_err_log_entries // 0" <<< "$json")

    # NVMe "data units" are 1000 × 512 bytes.
    info "  Power-on hours: $poh | Written: $(awk -v d="$duw" 'BEGIN {printf "%.1f TB", d * 512000 / 1e12}') | Unsafe shutdowns: $unsafe"

    (( cw != 0 ))              && fail "$dev: NVMe critical_warning = $cw (spare/temp/reliability/read-only flag set)"
    (( media > 0 ))            && fail "$dev: $media media/data integrity errors"
    (( spare < spare_th ))     && fail "$dev: available spare ${spare}% below threshold ${spare_th}%"
    if   (( used >= 90 )); then fail "$dev: ${used}% of rated endurance used"
    elif (( used >= 70 )); then warn "$dev: ${used}% of rated endurance used"
    else                        pass "$dev: wear ${used}%, spare ${spare}%, 0 media errors"
    fi
    # Error-log entries are often harmless (unsupported admin commands), so info only.
    (( errlog > 0 )) && info "  NVMe error-log entries: $errlog (usually benign; check 'nvme error-log' if other signs appear)"

    # A x4 drive running at x2/x1 = bad seat, adapter, or slot. Width doesn't
    # change at idle; link *speed* can (ASPM power saving), so only report it.
    ctrl=${name%n[0-9]*}                    # nvme0n1 → nvme0
    local pci="/sys/class/nvme/${ctrl}/device"
    if [[ -r "$pci/current_link_width" ]]; then
        local cw_l mw_l
        cw_l=$(<"$pci/current_link_width"); mw_l=$(<"$pci/max_link_width")
        info "  PCIe link: x${cw_l} of x${mw_l} @ $(<"$pci/current_link_speed")"
        if [[ "$cw_l" != "$mw_l" ]]; then
            warn "$dev: PCIe link running x${cw_l} (capable of x${mw_l}) — reseat the drive"
        else
            pass "$dev: PCIe link width x${cw_l}"
        fi
    fi
}

check_ata_health() {
    local dev=$1 json=$2 kind=$3
    local realloc pending offline crc uncorrect poh wear
    realloc=$(ata_attr "$json" 5);   pending=$(ata_attr "$json" 197)
    offline=$(ata_attr "$json" 198); crc=$(ata_attr "$json" 199)
    uncorrect=$(ata_attr "$json" 187)
    poh=$(jq -r '.power_on_time.hours // empty' <<< "$json")
    wear=$(jq -r '.endurance_used.current_percent // empty' <<< "$json")

    info "  Power-on hours: ${poh:-?} (~$(( ${poh:-0} / 8760 )) yrs) | Reallocated: ${realloc:-?} | Pending: ${pending:-?} | Offline-uncorrectable: ${offline:-?} | CRC: ${crc:-?}"
    [[ -n "$wear" ]] && info "  SSD endurance used: ${wear}%"

    local bad=0
    # Pending/uncorrectable = sectors the drive currently can't read. That's
    # active data loss, not history — always a fail.
    (( ${pending:-0} > 0 ))  && { fail "$dev: $pending pending (unreadable) sectors"; bad=1; }
    (( ${offline:-0} > 0 ))  && { fail "$dev: $offline offline-uncorrectable sectors"; bad=1; }
    if   (( ${realloc:-0} >= 10 )); then fail "$dev: $realloc reallocated sectors"; bad=1
    elif (( ${realloc:-0} > 0 ));  then warn "$dev: $realloc reallocated sectors — watch for growth"; bad=1
    fi
    (( ${uncorrect:-0} > 0 )) && { warn "$dev: $uncorrect reported-uncorrectable errors"; bad=1; }
    # CRC errors happen on the cable, not the platters.
    (( ${crc:-0} > 0 ))      && { warn "$dev: $crc interface CRC errors — replace/reseat the SATA cable"; bad=1; }
    if [[ $kind == hdd && -n "$poh" ]] && (( poh > HDD_HOURS_WARN )); then
        warn "$dev: ${poh} power-on hours — healthy now, but an old spinner; fine for backups, not primary storage"
        bad=1
    fi
    [[ -n "$wear" ]] && (( wear >= 90 )) && { fail "$dev: ${wear}% SSD endurance used"; bad=1; }
    (( bad == 0 )) && pass "$dev: no reallocated/pending/uncorrectable sectors"

    local cur max
    cur=$(jq -r '.interface_speed.current.string // empty' <<< "$json")
    max=$(jq -r '.interface_speed.max.string // empty' <<< "$json")
    if [[ -n "$cur" && -n "$max" && "$cur" != "$max" ]]; then
        warn "$dev: SATA link at $cur (drive supports $max) — cable, or a slower motherboard port"
    elif [[ -n "$cur" ]]; then
        info "  SATA link: $cur"
    fi
}

disk_bench() {
    local dev=$1 kind=$2
    command -v fio >/dev/null 2>&1 || return

    # --readonly makes fio refuse to issue any write or trim — that's what
    # makes benchmarking a raw device with data on it safe.
    local common=(--filename="$dev" --readonly --direct=1 --ioengine=libaio
                  --runtime="$FIO_SECONDS" --time_based --output-format=json)
    local seq_kib iops seq_mbs threshold
    seq_kib=$(fio --name=seq --rw=read --bs=1M --iodepth=16 "${common[@]}" 2>>"$LOG" \
              | jq -r '.jobs[0].read.bw // 0')
    iops=$(fio --name=rand --rw=randread --bs=4k --iodepth=32 "${common[@]}" 2>>"$LOG" \
              | jq -r '.jobs[0].read.iops // 0 | floor')
    seq_mbs=$(( ${seq_kib%.*} / 1024 ))
    info "  Read: ${seq_mbs} MiB/s sequential | ${iops} IOPS random 4K"

    case $kind in
        nvme) threshold=$NVME_SEQ_WARN_MBS ;;
        ssd)  threshold=$SSD_SEQ_WARN_MBS ;;
        *)    threshold=$HDD_SEQ_WARN_MBS ;;
    esac
    if (( seq_mbs < threshold )); then
        warn "$dev: sequential read ${seq_mbs} MiB/s is below ${threshold} expected for $kind"
    else
        pass "$dev: read throughput normal for $kind"
    fi
}

start_selftest() {
    local dev=$1 name ctrl code
    name=$(basename "$dev")
    if smartctl -t "$SMART_TEST" "$dev" >> "$LOG" 2>&1; then
        SELFTEST_DEVS+=("$dev"); SELFTEST_METHOD[$dev]="smartctl"
        info "  SMART $SMART_TEST self-test started (graded at the end)"
    elif [[ $name == nvme* ]] && command -v nvme >/dev/null 2>&1; then
        # Older smartmontools can't start NVMe self-tests; nvme-cli can.
        ctrl=${name%n[0-9]*}
        [[ $SMART_TEST == long ]] && code=2 || code=1
        if nvme device-self-test "/dev/$ctrl" -s "$code" >> "$LOG" 2>&1; then
            SELFTEST_DEVS+=("$dev"); SELFTEST_METHOD[$dev]="nvme"
            info "  NVMe $SMART_TEST self-test started via nvme-cli (graded at the end)"
        else
            info "  Drive does not support self-tests"
        fi
    else
        info "  Could not start a self-test on $dev"
    fi
}

test_disks() {
    section "3/7 Disk health"
    local dev name rota kind json rc passed
    local disks
    disks=$(list_disks)
    if [[ -z "$disks" ]]; then
        warn "No disks found"
        return
    fi

    for dev in $disks; do
        name=$(basename "$dev")
        rota=$(cat "/sys/block/$name/queue/rotational")
        if   [[ $name == nvme* ]]; then kind=nvme
        elif (( rota == 1 ));     then kind=hdd
        else                           kind=ssd
        fi
        echo
        info "$dev — $(lsblk -dno MODEL "$dev" | xargs) ($(lsblk -dno SIZE "$dev" | xargs), $kind)"

        # Declare first, assign second: `local x=$(cmd)` would make $? the
        # exit status of `local` (always 0) instead of cmd's.
        json=$(smartctl -j -a "$dev" 2>/dev/null)
        rc=$?
        echo "$json" > "${RUN_DIR}/smart-${name}.json"

        # smartctl's exit code is a bitmask — each bit means something
        # different (see `man smartctl`, EXIT STATUS). Test bits with &.
        if (( rc & 2 )); then warn "$dev: smartctl could not open the device"; continue; fi
        passed=$(jq -r '.smart_status.passed // "unknown"' <<< "$json")
        case $passed in
            true)  pass "$dev: SMART overall health PASSED" ;;
            false) fail "$dev: SMART overall health FAILED — replace this drive" ;;
            *)     warn "$dev: SMART overall health unavailable" ;;
        esac
        (( rc & 64 ))  && warn "$dev: drive's own error log has entries (see smart-${name}.json)"
        (( rc & 128 )) && warn "$dev: a previous self-test recorded a failure"

        if [[ $kind == nvme ]]; then
            check_nvme_health "$dev" "$json"
        else
            check_ata_health "$dev" "$json" "$kind"
        fi
        disk_bench "$dev" "$kind"
        start_selftest "$dev"
    done
}

# =============================================================================
# 4. Memory
# =============================================================================
test_memory() {
    section "4/7 Memory"

    # ZFS keeps its own cache (the ARC) outside the normal page cache. By
    # default it can grow to half of RAM, and it doesn't count as "available",
    # so memtester can't test that memory. Shrink it to its floor for the test,
    # then restore. (Proxmox installs on ZFS often; check: `arc_summary`.)
    if [[ -w "$ZFS_ARC_PARAM" ]]; then
        local c_min arc_now
        ZFS_ARC_ORIG=$(<"$ZFS_ARC_PARAM")
        c_min=$(awk '$1 == "c_min" {print $3}' /proc/spl/kstat/zfs/arcstats)
        # The limit must sit above c_min or ZFS silently ignores it.
        echo $(( c_min + 64 * 1024 * 1024 )) > "$ZFS_ARC_PARAM"
        info "ZFS detected — temporarily capping ARC at $(( c_min / 1048576 + 64 )) MB (was ${ZFS_ARC_ORIG}, 0 = auto)"
    fi

    # Flush the page cache so as much RAM as possible is free for testing.
    sync
    echo 3 > /proc/sys/vm/drop_caches
    if [[ -n "$ZFS_ARC_ORIG" ]]; then
        sleep 5   # ARC shrinks asynchronously
        arc_now=$(awk '$1 == "size" {print int($3 / 1048576)}' /proc/spl/kstat/zfs/arcstats)
        info "ZFS ARC now ${arc_now} MB"
    fi

    local avail_mb test_mb workers per_mb
    avail_mb=$(awk '/MemAvailable/ {print int($2 / 1024)}' /proc/meminfo)
    test_mb=$(( avail_mb * MEM_PCT / 100 ))
    workers=$(nproc)
    (( workers > 8 )) && workers=8
    per_mb=$(( test_mb / workers ))
    info "Available: ${avail_mb} MB → testing ${test_mb} MB as ${workers} parallel workers × ${per_mb} MB, ${MEM_PASSES} pass(es)"

    if ! command -v memtester >/dev/null 2>&1; then
        info "memtester unavailable — using stress-ng with result verification"
        if run stress-ng --vm "$workers" --vm-bytes "${per_mb}M" --vm-method all \
               --verify --timeout "$(( CPU_MINUTES * 60 / 2 ))s" --metrics-brief; then
            pass "stress-ng memory verification found no errors"
        else
            fail "stress-ng memory verification reported errors — suspect RAM"
        fi
        return
    fi

    # One memtester per core: each locks its own slice of RAM and runs the
    # full pattern suite on it. Same coverage, a fraction of the wall time.
    local pids=() i
    for (( i = 0; i < workers; i++ )); do
        memtester "${per_mb}M" "$MEM_PASSES" > "${RUN_DIR}/memtester-${i}.log" 2>&1 &
        pids+=($!)
    done

    local t0=$SECONDS running
    while :; do
        running=0
        for i in "${pids[@]}"; do kill -0 "$i" 2>/dev/null && running=$((running + 1)); done
        (( running == 0 )) && break
        printf '\r  %d/%d workers running — %d min elapsed ' "$running" "$workers" $(( (SECONDS - t0) / 60 ))
        sleep 15
    done
    echo

    local failed=0
    for i in "${pids[@]}"; do wait "$i" || failed=$((failed + 1)); done
    info "memtester finished in $(( (SECONDS - t0) / 60 )) min"

    # memtester quietly shrinks its allocation if it can't lock what it asked
    # for, so confirm how much was actually tested.
    local locked_mb
    locked_mb=$(grep -aho 'got  *[0-9]*MB' "${RUN_DIR}"/memtester-*.log | grep -o '[0-9]*' | awk '{s += $1} END {print s + 0}')
    (( locked_mb < test_mb * 9 / 10 )) && warn "memtester only locked ${locked_mb} of ${test_mb} MB requested"

    if (( failed > 0 )) || grep -qa "FAILURE" "${RUN_DIR}"/memtester-*.log; then
        fail "memtester found errors — defective RAM. Details: ${RUN_DIR}/memtester-*.log"
        grep -ah "FAILURE" "${RUN_DIR}"/memtester-*.log | head -5 | while IFS= read -r l; do info "  $l"; done
    else
        pass "memtester: ${locked_mb} MB, all pattern tests passed"
    fi
}

# =============================================================================
# 5. CPU
# =============================================================================
thermal_monitor() {   # runs in background, writes CSV every 5 s
    local csv=$1 phase
    while :; do
        phase=$(cat "${RUN_DIR}/.phase" 2>/dev/null || echo "?")
        echo "$(date +%s),${phase},$(pkg_temp),$(avg_mhz)" >> "$csv"
        sleep 5
    done
}

test_cpu() {
    section "5/7 CPU burn-in (${CPU_MINUTES} min)"
    modprobe coretemp 2>/dev/null

    local total=$(( CPU_MINUTES * 60 )) soak combined
    soak=$(( total * 2 / 3 )); combined=$(( total - soak ))

    local base_temp thr_before thr_after csv="${RUN_DIR}/thermal.csv"
    base_temp=$(pkg_temp)
    thr_before=$(throttle_total)
    info "Idle: ${base_temp:-?}°C @ $(avg_mhz) MHz avg"
    echo "epoch,phase,pkg_temp_c,avg_mhz" > "$csv"

    echo "soak" > "${RUN_DIR}/.phase"
    thermal_monitor "$csv" &
    local mon_pid=$!

    # Phase A — thermal soak. `0` = one worker per CPU thread. --verify makes
    # stress-ng check its own results: an overheating or marginal CPU produces
    # *wrong answers* before it crashes, and that's what we want to catch.
    info "Phase A: thermal soak, $(( soak / 60 )) min (cpu all-methods + matrix)"
    run stress-ng --cpu 0 --cpu-method all --matrix 0 --verify \
        --timeout "${soak}s" --metrics-brief
    local rc_a=$?

    # Phase B — CPU and memory controller together: maximum power draw,
    # which is what shakes out a weak PSU or VRM.
    echo "combined" > "${RUN_DIR}/.phase"
    info "Phase B: combined CPU + memory, $(( combined / 60 )) min"
    run stress-ng --cpu 0 --cpu-method all --vm 1 --vm-bytes 40% --vm-method all \
        --verify --timeout "${combined}s" --metrics-brief
    local rc_b=$?

    kill "$mon_pid" 2>/dev/null; wait "$mon_pid" 2>/dev/null
    thr_after=$(throttle_total)

    # stress-ng exit 2 = a stressor failed (including verification mismatch).
    if (( rc_a == 2 || rc_b == 2 )); then
        fail "stress-ng verification failures — CPU or RAM producing incorrect results"
    elif (( rc_a != 0 || rc_b != 0 )); then
        warn "stress-ng exited abnormally (codes $rc_a/$rc_b) — check the log"
    else
        pass "No computation errors under full load"
    fi

    local peak min_mhz max_mhz
    peak=$(tail -n +2 "$csv" | cut -d, -f3 | grep -v '^$' | sort -n | tail -1)
    min_mhz=$(tail -n +2 "$csv" | cut -d, -f4 | sort -n | head -1)
    max_mhz=$(tail -n +2 "$csv" | cut -d, -f4 | sort -n | tail -1)
    info "Peak package temp: ${peak:-?}°C | Avg clock under load: ${min_mhz}–${max_mhz} MHz"

    if [[ -z "$peak" ]]; then
        warn "Could not read CPU temperature (coretemp sensor not found)"
    elif (( peak >= TEMP_FAIL_C )); then
        fail "CPU hit ${peak}°C — cooling inadequate (dust, dried thermal paste, or fan)"
    elif (( peak >= TEMP_WARN_C )); then
        warn "CPU reached ${peak}°C — hot for sustained server duty; clean and consider repasting"
    else
        pass "CPU peak ${peak}°C — thermals fine"
    fi

    local thr_delta=$(( thr_after - thr_before ))
    if (( thr_delta > 0 )); then
        warn "CPU thermal-throttled during test (${thr_delta} events) — cooling can't sustain full load"
    else
        pass "No thermal throttling"
    fi
    info "Thermal log: $csv"
}

# =============================================================================
# 6. Network
# =============================================================================
test_network() {
    section "6/7 Network"
    local iface
    iface=$(ip -o route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
    if [[ -z "$iface" ]]; then
        warn "No default route — skipping network checks"
        return
    fi

    # Proxmox puts the host IP on a bridge (vmbr0). A bridge is software:
    # it reports no link speed and its counters aren't the NIC's. Follow it
    # to the physical port enslaved to it (the one with a real device/ link).
    if [[ -d "/sys/class/net/$iface/bridge" ]]; then
        local m phys=""
        for m in /sys/class/net/"$iface"/brif/*; do
            m=$(basename "$m")
            [[ -e "/sys/class/net/$m/device" ]] && { phys=$m; break; }
        done
        if [[ -n "$phys" ]]; then
            info "$iface is a bridge — testing its physical port $phys"
            iface=$phys
        fi
    fi

    local sysd="/sys/class/net/$iface" speed duplex
    speed=$(cat "$sysd/speed" 2>/dev/null || echo -1)
    duplex=$(cat "$sysd/duplex" 2>/dev/null || echo unknown)
    info "Interface: $iface — ${speed} Mb/s, $duplex duplex, driver $(basename "$(readlink -f "$sysd/device/driver")" 2>/dev/null)"

    if (( speed >= 1000 )) && [[ $duplex == full ]]; then
        pass "$iface linked at ${speed} Mb/s full duplex"
    elif (( speed > 0 )); then
        warn "$iface linked at ${speed} Mb/s $duplex — expected 1000/full; check cable (needs all 4 pairs) and switch port"
    else
        info "Link speed not reported (wireless or virtual interface?)"
    fi

    local rxe txe crc
    rxe=$(<"$sysd/statistics/rx_errors"); txe=$(<"$sysd/statistics/tx_errors")
    crc=$(cat "$sysd/statistics/rx_crc_errors" 2>/dev/null || echo 0)
    if (( rxe + txe + crc > 0 )); then
        warn "$iface error counters: rx=$rxe tx=$txe crc=$crc — usually cable or port"
    else
        pass "$iface: zero rx/tx/CRC errors"
    fi

    if [[ -n "$IPERF_SERVER" ]]; then
        if ! command -v iperf3 >/dev/null 2>&1; then warn "iperf3 not installed"; return; fi
        local up down
        up=$(iperf3 -c "$IPERF_SERVER" -t 10 -J 2>>"$LOG" | jq -r '.end.sum_received.bits_per_second // 0 | . / 1e6 | floor')
        down=$(iperf3 -c "$IPERF_SERVER" -t 10 -R -J 2>>"$LOG" | jq -r '.end.sum_received.bits_per_second // 0 | . / 1e6 | floor')
        info "iperf3 vs $IPERF_SERVER: upload ${up} Mb/s, download ${down} Mb/s"
        if (( up == 0 || down == 0 )); then
            warn "iperf3 failed — is 'iperf3 -s' running on $IPERF_SERVER and port 5201 open?"
        elif (( up < 800 || down < 800 )) && (( speed >= 1000 )); then
            warn "Throughput below ~800 Mb/s on a gigabit link"
        else
            pass "iperf3 throughput healthy"
        fi
    else
        info "iperf3 skipped (use --iperf HOST to test throughput)"
    fi
}

# =============================================================================
# 7. Post-flight
# =============================================================================
check_selftests() {
    local dev out line name ctrl result
    for dev in "${SELFTEST_DEVS[@]}"; do
        name=$(basename "$dev")
        if [[ ${SELFTEST_METHOD[$dev]} == nvme ]]; then
            ctrl=${name%n[0-9]*}
            out=$(nvme self-test-log "/dev/$ctrl" 2>&1)
            if grep -qiE 'Current operation *: *0x?[1-9]' <<< "$out"; then
                info "$dev: self-test still running — check later: nvme self-test-log /dev/$ctrl"
                continue
            fi
            result=$(grep -m1 'Operation Result' <<< "$out" | awk '{print $NF}')
            case $(( ${result:-15} )) in
                0)     pass "$dev: NVMe self-test completed without error" ;;
                5|6|7) fail "$dev: NVMe self-test FAILED (result code $result)" ;;
                *)     warn "$dev: NVMe self-test inconclusive (result code ${result:-none})" ;;
            esac
            continue
        fi

        out=$(smartctl -l selftest -c "$dev" 2>&1)
        if grep -qiE 'self-test routine in progress|self-test in progress \(' <<< "$out"; then
            info "$dev: self-test still running — check later: sudo smartctl -l selftest $dev"
            continue
        fi
        # First result row: "# 1 ..." on SATA, " 0 ..." on NVMe.
        line=$(grep -m1 -E '^(# ?1|[[:space:]]*0)[[:space:]]+[A-Za-z]' <<< "$out")
        if [[ $line == *"Completed without error"* ]]; then
            pass "$dev: SMART self-test completed without error"
        elif grep -qiE 'abort|interrupt' <<< "$line"; then
            warn "$dev: SMART self-test was aborted/interrupted — rerun: smartctl -t short $dev"
        elif grep -qiE 'fail|error' <<< "$line"; then
            fail "$dev: SMART self-test FAILED: $line"
        else
            warn "$dev: couldn't determine self-test result — check: smartctl -l selftest $dev"
        fi
    done
}

test_postflight() {
    section "7/7 Post-flight"
    local new severe
    new=$(hw_errors "$START_EPOCH")
    if [[ -z "$new" ]]; then
        pass "No new hardware errors logged during the test"
    else
        echo "$new" > "${RUN_DIR}/new-kernel-errors.txt"
        severe=$(grep -Ei "$HW_ERR_SEVERE" <<< "$new" || true)
        if [[ -n "$severe" ]]; then
            fail "Machine-check / memory errors appeared under load (see new-kernel-errors.txt)"
        else
            warn "$(wc -l <<< "$new") new hardware-related kernel messages during test (see new-kernel-errors.txt)"
        fi
        head -10 <<< "$new" | while IFS= read -r l; do info "  $l"; done
    fi

    check_selftests
    info "Cooled to: $(pkg_temp)°C"
}

# =============================================================================
# Summary
# =============================================================================
summary() {
    local elapsed=$(( ($(date +%s) - START_EPOCH) / 60 )) w f verdict code
    if   (( ${#FAILURES[@]} > 0 )); then verdict="${C_R}FAIL — do not deploy until resolved${C_N}"; code=2
    elif (( ${#WARNINGS[@]} > 0 )); then verdict="${C_Y}PASS WITH WARNINGS — review below${C_N}"; code=1
    else                                 verdict="${C_G}PASS — hardware is healthy${C_N}"; code=0
    fi

    section "Summary — ${HOST_SHORT} ($(dmidecode -s system-product-name)), ${elapsed} min"
    {
        echo "Host:     ${HOST_SHORT}"
        echo "System:   $(dmidecode -s system-manufacturer) $(dmidecode -s system-product-name) (S/N $(dmidecode -s system-serial-number))"
        echo "Date:     $(date)"
        echo "Duration: ${elapsed} min"
        echo "Passed:   ${PASS_N}   Warnings: ${#WARNINGS[@]}   Failures: ${#FAILURES[@]}"
        for f in "${FAILURES[@]}"; do echo "  FAIL  $f"; done
        for w in "${WARNINGS[@]}"; do echo "  WARN  $w"; done
    } | tee "${RUN_DIR}/summary.txt" | tee -a "$LOG"
    echo
    echo "Verdict: $verdict"
    echo "Logs:    ${RUN_DIR}/"
    exit "$code"
}

# =============================================================================
# Main
# =============================================================================
main() {
    parse_args "$@"
    require_root
    mkdir -p "$RUN_DIR"
    check_environment

    printf '%s== Hardware acceptance test — %s ==%s\n' "$C_G" "$HOST_SHORT" "$C_N"
    echo "CPU stress: ${CPU_MINUTES} min | RAM tested: ${MEM_PCT}% of available | SMART self-test: ${SMART_TEST}"
    echo "Expect roughly $(( CPU_MINUTES + 10 )) min plus the memory test (scales with RAM: ~10–15 min for 8 GB, ~30–45 for 32 GB)."
    echo "The machine will be fully loaded — don't run it alongside production work."
    if (( ! ASSUME_YES )); then
        read -rp "Press ENTER to start, Ctrl+C to cancel... "
    fi

    install_deps
    system_inventory
    test_preflight
    test_disks
    test_memory
    restore_zfs_arc
    test_cpu
    test_network
    test_postflight
    summary
}

main "$@"
