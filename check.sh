#!/bin/bash
#
# check-kernel-compat.sh
# Detects distro + version, checks if running kernel is adequate,
# and suggests what to do if it isn't.
#
# Works on: Ubuntu, Fedora, openSUSE, Debian, Rocky, Alma, RHEL, etc.
#

set -euo pipefail

# ---------- Distro Detection ----------
if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    . /etc/os-release
else
    echo "ERROR: /etc/os-release not found. This script requires a modern Linux distro."
    exit 1
fi

DISTRO_ID="${ID:-unknown}"
DISTRO_VERSION="${VERSION_ID:-unknown}"
PRETTY="${PRETTY_NAME:-$DISTRO_ID $DISTRO_VERSION}"

# Normalize distro ID
case "$DISTRO_ID" in
    ubuntu|debian|linuxmint|pop|elementary) DISTRO_FAMILY="debian" ;;
    fedora|centos|rhel|rocky|almalinux)     DISTRO_FAMILY="rhel" ;;
    opensuse*|suse)                         DISTRO_FAMILY="suse" ;;
    *)                                      DISTRO_FAMILY="unknown" ;;
esac

# ---------- Kernel Detection ----------
CURRENT_KERNEL=$(uname -r | cut -d- -f1)          # e.g. 6.8.0
CURRENT_MAJOR_MINOR=$(echo "$CURRENT_KERNEL" | cut -d. -f1-2)  # e.g. 6.8

echo "Distro : $PRETTY"
echo "Kernel : $CURRENT_KERNEL (major.minor = $CURRENT_MAJOR_MINOR)"
echo

# ---------- Minimum Kernel Lookup Table ----------
# Format: distro_id:version_id:min_kernel_major.minor
# You can easily extend this table

declare -A MIN_KERNELS=(
    # Ubuntu
    ["ubuntu:20.04"]="5.4"
    ["ubuntu:22.04"]="5.15"
    ["ubuntu:24.04"]="6.8"

    # Debian
    ["debian:11"]="5.10"
    ["debian:12"]="6.1"

    # Fedora
    ["fedora:38"]="6.2"
    ["fedora:39"]="6.5"
    ["fedora:40"]="6.8"
    ["fedora:41"]="6.11"

    # openSUSE Leap
    ["opensuse-leap:15.5"]="5.14"
    ["opensuse-leap:15.6"]="6.4"

    # RHEL / Rocky / Alma
    ["rocky:8"]="4.18"
    ["rocky:9"]="5.14"
    ["almalinux:8"]="4.18"
    ["almalinux:9"]="5.14"
)

KEY="${DISTRO_ID}:${DISTRO_VERSION}"
MIN_KERNEL="${MIN_KERNELS[$KEY]:-}"

if [[ -z "$MIN_KERNEL" ]]; then
    echo "No specific minimum kernel defined for $PRETTY in this script."
    echo "Using a conservative default of 5.4 for safety."
    MIN_KERNEL="5.4"
fi

echo "Minimum recommended kernel for this release: $MIN_KERNEL"

# ---------- Version Comparison ----------
# Returns 0 if $1 >= $2 (using sort -V)
version_ge() {
    printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1 | grep -qx "$2"
}

if version_ge "$CURRENT_MAJOR_MINOR" "$MIN_KERNEL"; then
    echo
    echo "✅ Your kernel ($CURRENT_MAJOR_MINOR) meets or exceeds the minimum for $PRETTY."
else
    echo
    echo "❌ Your kernel ($CURRENT_MAJOR_MINOR) is OLDER than recommended for $PRETTY."
    echo "   Minimum required: $MIN_KERNEL"

    # Suggest an older distro version that would work with current kernel
    echo
    echo "Suggestions:"

    # Find a compatible older version (very simple heuristic)
    case "$DISTRO_ID" in
        ubuntu)
            if version_ge "5.15" "$CURRENT_MAJOR_MINOR"; then
                echo "  → You can safely run Ubuntu 22.04 LTS with your kernel."
            elif version_ge "5.4" "$CURRENT_MAJOR_MINOR"; then
                echo "  → You can safely run Ubuntu 20.04 LTS with your kernel."
            else
                echo "  → Consider Ubuntu 18.04 or upgrade your kernel."
            fi
            ;;
        fedora)
            echo "  → Fedora moves fast. Consider Fedora 38/39 or upgrade the kernel."
            ;;
        opensuse-leap)
            echo "  → Try openSUSE Leap 15.5 or earlier."
            ;;
        *)
            echo "  → Try an older release of this distro or upgrade the kernel."
            ;;
    esac

    echo
    echo "Alternative: Upgrade your kernel (recommended for security & features)."
    echo "  On Ubuntu:   sudo apt update && sudo apt install linux-generic-hwe-24.04"
    echo "  On Fedora:   sudo dnf upgrade kernel"
    echo "  On openSUSE: sudo zypper up kernel-default"
fi
