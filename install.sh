#!/usr/bin/env bash
#
# adbshot installer.
#
#   curl -fsSL https://raw.githubusercontent.com/acosta290/adbshot/main/install.sh | bash
#
# Detects your distribution, installs what is missing, puts adbshot in place and
# leaves it running. Safe to re-run: every step is idempotent.
#
# Options (pass after `| bash -s --`):
#   --user            install into ~/.local, no root at all
#   --prefix DIR      install somewhere else (implies a system install if outside $HOME)
#   --no-deps         skip package installation, only build and install
#   --ref REF         git branch or tag to install (default: main)
#   --uninstall       remove adbshot again
#   --yes             do not ask for confirmation
#
set -euo pipefail

REPO_URL="${ADBSHOT_REPO:-https://github.com/acosta290/adbshot}"
REF="${ADBSHOT_REF:-main}"
PREFIX=""
USER_MODE=0
SKIP_DEPS=0
UNINSTALL=0
ASSUME_YES=0

# ---------------------------------------------------------------- output ----

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BLUE=$'\033[34m'
else
    B=""; DIM=""; R=""; GREEN=""; YELLOW=""; RED=""; BLUE=""
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s%s\n' "$BLUE" "$R" "$B" "$*" "$R"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$R" "$*"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$R" "$*"; }
die()  { printf '\n  %s✗%s %s\n\n' "$RED" "$R" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------- arguments ----

while [ $# -gt 0 ]; do
    case "$1" in
        --user)      USER_MODE=1 ;;
        --prefix)    PREFIX="${2:?--prefix needs a directory}"; shift ;;
        --no-deps)   SKIP_DEPS=1 ;;
        --ref)       REF="${2:?--ref needs a branch or tag}"; shift ;;
        --uninstall) UNINSTALL=1 ;;
        --yes|-y)    ASSUME_YES=1 ;;
        -h|--help)
            # Print the header comment block, stopping at the first real line.
            awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
            exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done

if [ "$USER_MODE" = 1 ] && [ -z "$PREFIX" ]; then
    PREFIX="$HOME/.local"
fi
[ -n "$PREFIX" ] || PREFIX="/usr/local"

case "$PREFIX" in
    "$HOME"/*) NEEDS_ROOT=0 ;;
    *)         NEEDS_ROOT=1 ;;
esac

# --------------------------------------------------------- sudo handling ----

SUDO=""
if [ "$(id -u)" != "0" ]; then
    if have sudo; then
        SUDO="sudo"
    elif [ "$NEEDS_ROOT" = 1 ]; then
        die "root is needed for a system install and sudo was not found.
     Try a personal install instead:  ... | bash -s -- --user"
    fi
fi

confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    [ -t 0 ] || return 0          # piped from curl: nothing to read from
    printf '\n  %s [Y/n] ' "$1"
    read -r reply </dev/tty || return 0
    case "$reply" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# ------------------------------------------------------------ the system ----

detect_os() {
    OS_ID="unknown"; OS_NAME="unknown"; OS_LIKE=""
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_NAME="${PRETTY_NAME:-$OS_ID}"
        OS_LIKE="${ID_LIKE:-}"
    fi
    for family in "$OS_ID" $OS_LIKE; do
        case "$family" in
            debian|ubuntu)          PKG=apt;    break ;;
            fedora|rhel|centos)     PKG=dnf;    break ;;
            arch|archlinux)         PKG=pacman; break ;;
            opensuse*|suse|sles)    PKG=zypper; break ;;
        esac
    done
    # Fall back to whatever package manager is actually present.
    if [ -z "${PKG:-}" ]; then
        for candidate in apt-get dnf pacman zypper; do
            if have "$candidate"; then
                case "$candidate" in apt-get) PKG=apt ;; *) PKG="$candidate" ;; esac
                break
            fi
        done
    fi
    PKG="${PKG:-none}"
}

packages_for() {
    case "$1" in
        apt)    echo "python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-notify-0.7 \
gir1.2-gsound-1.0 python3-pil python3-numpy wl-clipboard adb make git" ;;
        dnf)    echo "python3-gobject gtk4 libadwaita libnotify gsound \
python3-pillow python3-numpy wl-clipboard android-tools make git" ;;
        pacman) echo "python-gobject gtk4 libadwaita libnotify gsound \
python-pillow python-numpy wl-clipboard android-tools make git" ;;
        zypper) echo "python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-4_0 \
typelib-1_0-Adw-1 typelib-1_0-Notify-0_7 typelib-1_0-GSound-1_0 python3-Pillow \
python3-numpy wl-clipboard android-tools make git" ;;
        *)      echo "" ;;
    esac
}

install_packages() {
    local pkgs; pkgs="$(packages_for "$PKG")"
    [ -n "$pkgs" ] || { warn "unknown package manager; skipping dependency install"; return 0; }
    say "  ${DIM}$(echo "$pkgs" | tr -s ' ')${R}"
    confirm "Install these with $PKG?" || { warn "skipped"; return 0; }
    # shellcheck disable=SC2086
    case "$PKG" in
        apt)    $SUDO apt-get update -qq && $SUDO apt-get install -y $pkgs ;;
        dnf)    $SUDO dnf install -y $pkgs ;;
        pacman) $SUDO pacman -Sy --needed --noconfirm $pkgs ;;
        zypper) $SUDO zypper --non-interactive install $pkgs ;;
    esac
}

# What actually matters is whether Python can import these, not whether a
# package name we guessed exists. This is the check that decides.
verify_runtime() {
    python3 - <<'PY'
import sys
missing = []
try:
    import gi
except ImportError:
    print("python3-gi (the gi module) is missing"); sys.exit(1)
for ns, ver, why in (
    ("Gtk", "4.0", "the interface"),
    ("Adw", "1",   "the interface"),
    ("Notify", "0.7", "notifications"),
    ("GSound", "1.0", "the shutter sound"),
):
    try:
        gi.require_version(ns, ver)
        __import__("gi.repository." + ns)
    except Exception:
        missing.append(f"{ns} {ver} ({why})")
for mod, why in (("PIL", "format, quality and scale"), ("numpy", "black-capture detection")):
    try:
        __import__(mod)
    except ImportError:
        missing.append(f"python {mod} ({why})")
if missing:
    print("missing: " + ", ".join(missing)); sys.exit(1)
try:
    from gi.repository import Adw
    if (Adw.MAJOR_VERSION, Adw.MINOR_VERSION) < (1, 5):
        print(f"libadwaita {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION} is too old; 1.5 is required")
        sys.exit(2)
except Exception:
    pass
PY
}

# ---------------------------------------------------------------- sources ---

fetch_sources() {
    # Already inside a checkout? Use it.
    if [ -f "./Makefile" ] && [ -f "./src/adbshot/const.py" ]; then
        SRC="$(pwd)"
        ok "using this checkout: $SRC"
        return 0
    fi
    SRC="${TMPDIR:-/tmp}/adbshot-install.$$"
    if have git; then
        git clone --depth 1 --branch "$REF" "$REPO_URL" "$SRC" >/dev/null 2>&1 \
            || die "could not clone $REPO_URL (branch $REF)"
        ok "cloned $REPO_URL@$REF"
    elif have curl && have tar; then
        mkdir -p "$SRC"
        curl -fsSL "$REPO_URL/archive/refs/heads/$REF.tar.gz" \
            | tar -xz -C "$SRC" --strip-components=1 \
            || die "could not download $REPO_URL@$REF"
        ok "downloaded $REPO_URL@$REF"
    else
        die "need git, or curl and tar, to fetch the sources"
    fi
    CLEANUP_SRC="$SRC"
}

cleanup() { [ -n "${CLEANUP_SRC:-}" ] && rm -rf "$CLEANUP_SRC"; }
trap cleanup EXIT

# ------------------------------------------------------------------ main ----

say ""
say "  ${B}adbshot${R} ${DIM}— phone screenshots straight to your clipboard${R}"

detect_os

if [ "$UNINSTALL" = 1 ]; then
    step "Removing adbshot"
    fetch_sources
    if [ "$NEEDS_ROOT" = 1 ]; then
        (cd "$SRC" && $SUDO make uninstall PREFIX="$PREFIX")
    else
        (cd "$SRC" && make uninstall PREFIX="$PREFIX")
    fi
    say ""
    ok "adbshot removed. Your screenshots and settings were left alone."
    say ""
    exit 0
fi

step "Checking your system"
say "  ${DIM}$OS_NAME${R}"

case "${XDG_SESSION_TYPE:-}" in
    wayland) ok "Wayland session" ;;
    x11)     warn "X11 session — adbshot targets Wayland; the tray and clipboard may misbehave" ;;
    *)       warn "could not detect the session type (XDG_SESSION_TYPE is unset)" ;;
esac

case "${XDG_CURRENT_DESKTOP:-}" in
    *GNOME*) ok "GNOME desktop" ;;
    "")      warn "could not detect the desktop" ;;
    *)       warn "desktop is ${XDG_CURRENT_DESKTOP}; adbshot targets GNOME" ;;
esac

if [ "$NEEDS_ROOT" = 1 ]; then
    ok "installing for every user, into $PREFIX ${DIM}(needs sudo)${R}"
else
    ok "installing for $USER only, into $PREFIX ${DIM}(no root)${R}"
fi

if [ "$SKIP_DEPS" = 0 ]; then
    step "Installing dependencies"
    if [ "$PKG" = "none" ]; then
        warn "no supported package manager found; install the dependencies yourself"
    else
        install_packages
    fi
else
    step "Skipping dependencies (--no-deps)"
fi

step "Verifying the runtime"
if out="$(verify_runtime 2>&1)"; then
    ok "every Python dependency imports"
else
    rc=$?
    say "  ${DIM}${out}${R}"
    if [ "$rc" = 2 ]; then
        die "your libadwaita is older than 1.5, so adbshot cannot run here.
     You need GNOME 46 or newer (Ubuntu 24.04+, Fedora 40+)."
    fi
    die "dependencies are still missing. Install them and re-run this script."
fi

have adb || warn "adb is not on PATH yet — captures will fail until it is"

step "Installing adbshot"
fetch_sources
if [ "$NEEDS_ROOT" = 1 ]; then
    $SUDO make -C "$SRC" install PREFIX="$PREFIX"
else
    make -C "$SRC" install PREFIX="$PREFIX"
fi

step "Checking the installation"
ADBSHOT="$PREFIX/bin/adbshot"
if [ -x "$ADBSHOT" ]; then
    "$ADBSHOT" --self-check || true
else
    warn "$ADBSHOT is not executable — something went wrong"
fi

say ""
say "  ${GREEN}${B}Done.${R} Press ${B}Super+X${R} with your phone plugged in over USB."
say "  ${DIM}The tray icon sits next to the clock; click it for settings.${R}"
say ""
if ! have adbshot; then
    say "  ${YELLOW}Add this to your shell profile so the CLI is on PATH:${R}"
    say "    export PATH=\"$PREFIX/bin:\$PATH\""
    say ""
fi
