#!/usr/bin/env bash
# Installation script for OneDrive Sync Client (ODSC)
#
# End-user installation (no git required):
#   curl -fsSL https://github.com/marlobello/odsc/releases/latest/download/install.sh | bash
#   bash install.sh               # installs latest release
#   bash install.sh 1.2.0         # installs a specific version
#
# Developer installation (from a local checkout):
#   bash install.sh --dev         # editable install, no download

set -euo pipefail

GITHUB_REPO="marlobello/odsc"
# Stamped with the actual version by the release workflow; stays as a
# placeholder when running from a local development checkout.
BAKED_VERSION="__ODSC_RELEASE_VERSION__"

# ── Argument parsing ───────────────────────────────────────────────────────────
DEV_MODE=false
REQUESTED_VERSION=""

for arg in "$@"; do
    case "$arg" in
        --dev) DEV_MODE=true ;;
        --help|-h)
            echo "Usage: install.sh [--dev] [VERSION]"
            echo "  --dev      Install from local checkout in editable mode (for developers)"
            echo "  VERSION    Install a specific release version (e.g. 1.2.0)"
            exit 0 ;;
        -*)
            echo "Unknown option: $arg" >&2; exit 1 ;;
        *)
            REQUESTED_VERSION="$arg" ;;
    esac
done

echo "==================================="
echo "OneDrive Sync Client Installation"
echo "==================================="
echo ""

# Check if running on Linux
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    echo "Error: This script is designed for Linux systems"
    exit 1
fi

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
required_version="3.8"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "Error: Python 3.8 or higher is required (found: $python_version)"
    exit 1
fi
echo "✓ Python $python_version found"

# ── System dependencies ────────────────────────────────────────────────────────
echo ""
echo "Installing system dependencies..."
if command -v apt-get &> /dev/null; then
    echo "Detected Debian/Ubuntu system"
    sudo apt-get update -q
    sudo apt-get install -y \
        python3-pip \
        python3-gi \
        python3-gi-cairo \
        gir1.2-gtk-3.0 \
        gir1.2-gio-2.0 \
        gir1.2-appindicator3-0.1 \
        libadwaita-1-0 \
        gir1.2-adw-1 \
        python3-dbus \
        python3-requests \
        python3-watchdog \
        python3-dateutil \
        python3-send2trash \
        python3-cryptography \
        python3-keyring \
        python3-certifi \
        python3-tenacity
    echo "✓ System dependencies installed"
elif command -v dnf &> /dev/null; then
    echo "Detected Fedora/RHEL system"
    sudo dnf install -y \
        python3-pip \
        python3-gobject \
        gtk3 \
        libappindicator-gtk3 \
        libadwaita \
        python3-dbus \
        python3-requests \
        python3-watchdog \
        python3-dateutil \
        python3-send2trash \
        python3-cryptography \
        python3-keyring \
        python3-certifi \
        python3-tenacity
    echo "✓ System dependencies installed"
else
    echo "⚠ Could not detect package manager. Please install manually:"
    echo "  GTK 3 (python3-gi), D-Bus (python3-dbus),"
    echo "  requests, watchdog, dateutil, send2trash, cryptography, keyring"
fi

# ── Resolve source directory ───────────────────────────────────────────────────
# In dev mode or when run from inside a repo checkout, SOURCE_DIR is the repo
# root and we do an editable install.  Otherwise we download the release tarball
# and extract it to a temp directory.

TMPDIR_CREATED=false
SOURCE_DIR=""

if $DEV_MODE; then
    # Developer mode: use the directory containing this script as source
    SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    echo ""
    echo "Developer mode: installing from local checkout ($SOURCE_DIR)"
else
    # ── Determine version to download ─────────────────────────────────────────
    if [[ -n "$REQUESTED_VERSION" ]]; then
        VERSION="$REQUESTED_VERSION"
    elif [[ "$BAKED_VERSION" != "__ODSC_RELEASE_VERSION__" ]]; then
        VERSION="$BAKED_VERSION"
    else
        echo ""
        echo "Fetching latest release version..."
        VERSION=$(curl -fsSL "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" \
            | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))")
        if [[ -z "$VERSION" ]]; then
            echo "Error: could not determine the latest release version."
            exit 1
        fi
    fi

    TARBALL="odsc-${VERSION}.tar.gz"
    DOWNLOAD_URL="https://github.com/${GITHUB_REPO}/releases/download/v${VERSION}/${TARBALL}"

    echo ""
    echo "Downloading ODSC v${VERSION}..."
    WORK_DIR=$(mktemp -d)
    TMPDIR_CREATED=true
    trap 'rm -rf "$WORK_DIR"' EXIT

    curl -fsSL "$DOWNLOAD_URL" -o "$WORK_DIR/$TARBALL"
    echo "✓ Downloaded $TARBALL"

    echo "Extracting..."
    tar -xzf "$WORK_DIR/$TARBALL" -C "$WORK_DIR"
    # sdist extracts to odsc-VERSION/
    SOURCE_DIR="$WORK_DIR/odsc-${VERSION}"
    echo "✓ Extracted to $SOURCE_DIR"
fi

# ── Install ODSC Python package ────────────────────────────────────────────────
echo ""
echo "Installing ODSC..."
if $DEV_MODE; then
    pip3 install --user --break-system-packages -e "$SOURCE_DIR" --no-deps
    echo "✓ ODSC installed in editable (developer) mode"
else
    pip3 install --user --break-system-packages "$SOURCE_DIR"
    echo "✓ ODSC v${VERSION} installed"
fi

# ── Create sync directory ──────────────────────────────────────────────────────
echo ""
echo "Creating default sync directory..."
mkdir -p "$HOME/OneDrive"
echo "✓ Sync directory created at $HOME/OneDrive"

# ── Icons ──────────────────────────────────────────────────────────────────────
echo ""
echo "Installing application icon..."
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$HOME/.local/share/icons/hicolor/128x128/apps"
cp "$SOURCE_DIR/desktop/odsc.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/"
cp "$SOURCE_DIR/desktop/odsc.png" "$HOME/.local/share/icons/hicolor/128x128/apps/"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "✓ Application icon installed"

# ── Systemd service ────────────────────────────────────────────────────────────
echo ""
read -p "Install systemd service for background sync? [Y/n] " -n 1 -r
echo
if [[ -z $REPLY ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/.config/systemd/user"
    cp "$SOURCE_DIR/systemd/odsc.service" "$HOME/.config/systemd/user/"
    systemctl --user daemon-reload
    echo "✓ Systemd service installed"

    echo ""
    read -p "Enable service to start automatically on login? [Y/n] " -n 1 -r
    echo
    if [[ -z $REPLY ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
        systemctl --user enable odsc
        echo "✓ Service enabled for auto-start"
    fi

    read -p "Start service now? [Y/n] " -n 1 -r
    echo
    if [[ -z $REPLY ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
        systemctl --user start odsc
        echo "✓ Service started"
    fi
else
    echo "Skipped systemd service installation"
fi

# ── Desktop entry ──────────────────────────────────────────────────────────────
echo ""
read -p "Install desktop application entry for GUI? [Y/n] " -n 1 -r
echo
if [[ -z $REPLY ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/.local/share/applications"
    cp "$SOURCE_DIR/desktop/odsc.desktop" "$HOME/.local/share/applications/com.github.odsc.desktop"

    if command -v desktop-file-validate &> /dev/null; then
        desktop-file-validate "$HOME/.local/share/applications/com.github.odsc.desktop" 2>&1 | grep -i error || true
    fi

    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    rm -rf "$HOME/.cache/icon-cache.kcache" 2>/dev/null || true
    touch "$HOME/.local/share/applications/com.github.odsc.desktop"

    if [ "${XDG_CURRENT_DESKTOP:-}" = "ubuntu:GNOME" ] || [ "${XDG_CURRENT_DESKTOP:-}" = "GNOME" ]; then
        echo "✓ Desktop entry installed"
        echo ""
        echo "To make the application appear in GNOME:"
        echo "  Option 1: Press Alt+F2, type 'r', and press Enter"
        echo "  Option 2: Log out and log back in"
    else
        echo "✓ Desktop entry installed"
    fi
else
    echo "Skipped desktop entry installation"
fi

echo ""
echo "==================================="
echo "Installation completed successfully!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. Launch ODSC GUI from your applications menu or run: odsc-gui"
echo "2. Go to Authentication → Login to connect your Microsoft account"
echo "3. Configure sync settings in Settings → Preferences"
echo "4. Start syncing!"
echo ""
echo "Useful commands:"
echo "  odsc-gui                        - Launch the GUI application"
echo "  odsc --version                  - Show installed version"
echo "  odsc update                     - Check for newer releases"
echo "  odsc status                     - View sync status"
echo "  odsc auth                       - Authenticate from command line"
echo "  systemctl --user status odsc    - Check daemon status"
echo ""
echo "To upgrade to a newer version, re-run:"
echo "  curl -fsSL https://github.com/${GITHUB_REPO}/releases/latest/download/install.sh | bash"
