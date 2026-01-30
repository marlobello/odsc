#!/bin/bash
# Installation script for OneDrive Sync Client (ODSC)

set -e

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

# Install system dependencies
echo ""
echo "Installing system dependencies and Python packages..."
if command -v apt-get &> /dev/null; then
    echo "Detected Debian/Ubuntu system"
    sudo apt-get update
    sudo apt-get install -y \
        python3-pip \
        python3-gi \
        python3-gi-cairo \
        gir1.2-gtk-3.0 \
        gir1.2-gio-2.0 \
        gir1.2-appindicator3-0.1 \
        python3-dbus \
        python3-requests \
        python3-watchdog \
        python3-dateutil \
        python3-send2trash \
        python3-cryptography \
        python3-keyring \
        python3-certifi
    echo "✓ All system dependencies and Python packages installed"
elif command -v dnf &> /dev/null; then
    echo "Detected Fedora/RHEL system"
    sudo dnf install -y \
        python3-pip \
        python3-gobject \
        gtk3 \
        libappindicator-gtk3 \
        python3-dbus \
        python3-requests \
        python3-watchdog \
        python3-dateutil \
        python3-send2trash \
        python3-cryptography \
        python3-keyring \
        python3-certifi
    echo "✓ All system dependencies and Python packages installed"
else
    echo "Warning: Could not detect package manager."
    echo "Please install the following dependencies manually:"
    echo "  - Python 3.8+"
    echo "  - GTK 3 (python3-gi)"
    echo "  - D-Bus (python3-dbus)"
    echo "  - Python packages: requests, watchdog, dateutil, send2trash"
fi

# Install ODSC package in editable mode
echo ""
echo "Installing ODSC..."
pip3 install --user --break-system-packages -e . --no-deps
echo "✓ ODSC installed in editable mode"

# Create sync directory
echo ""
echo "Creating default sync directory..."
mkdir -p "$HOME/OneDrive"
echo "✓ Sync directory created at $HOME/OneDrive"

# Install icon
echo ""
echo "Installing application icon..."
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$HOME/.local/share/icons/hicolor/128x128/apps"
cp desktop/odsc.svg "$HOME/.local/share/icons/hicolor/scalable/apps/"
cp desktop/odsc.png "$HOME/.local/share/icons/hicolor/128x128/apps/"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "✓ Application icon installed (SVG + PNG)"

# Install systemd service
echo ""
read -p "Install systemd service for background sync? [Y/n] " -n 1 -r
echo
# Default to yes if user just presses enter
if [[ -z $REPLY ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/.config/systemd/user"
    cp systemd/odsc.service "$HOME/.config/systemd/user/"
    systemctl --user daemon-reload
    echo "✓ Systemd service installed"
    
    # Ask about enabling on startup
    echo ""
    read -p "Enable service to start automatically on login? [Y/n] " -n 1 -r
    echo
    if [[ -z $REPLY ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
        systemctl --user enable odsc
        echo "✓ Service enabled for auto-start"
    fi
    
    # Ask about starting now
    read -p "Start service now? [Y/n] " -n 1 -r
    echo
    if [[ -z $REPLY ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
        systemctl --user start odsc
        echo "✓ Service started"
    fi
else
    echo "Skipped systemd service installation"
fi

# Install desktop entry for GUI
echo ""
read -p "Install desktop application entry for GUI? [Y/n] " -n 1 -r
echo
# Default to yes if user just presses enter
if [[ -z $REPLY ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/.local/share/applications"
    # Use application ID name for proper GNOME integration
    cp desktop/odsc.desktop "$HOME/.local/share/applications/com.github.odsc.desktop"
    
    # Validate desktop file
    if command -v desktop-file-validate &> /dev/null; then
        if desktop-file-validate "$HOME/.local/share/applications/com.github.odsc.desktop" 2>&1 | grep -i error; then
            echo "⚠ Desktop file validation found errors"
        fi
    fi
    
    # Update desktop database
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    
    # Clear icon cache to ensure fresh icon loads
    rm -rf "$HOME/.cache/icon-cache.kcache" 2>/dev/null || true
    
    # Touch the file to update timestamp (helps GNOME detect changes)
    touch "$HOME/.local/share/applications/com.github.odsc.desktop"
    
    # For GNOME Shell, we need to restart it or the user needs to log out
    if [ "$XDG_CURRENT_DESKTOP" = "ubuntu:GNOME" ] || [ "$XDG_CURRENT_DESKTOP" = "GNOME" ]; then
        echo "✓ Desktop entry installed"
        echo ""
        echo "To make the application appear in GNOME:"
        echo "  Option 1: Press Alt+F2, type 'r', and press Enter (restart GNOME Shell)"
        echo "  Option 2: Log out and log back in"
        echo "  Option 3: Search for 'OneDrive' in the Activities menu"
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
echo "  odsc-gui           - Launch the GUI application"
echo "  odsc status        - View sync status"
echo "  odsc auth          - Authenticate from command line"
echo "  systemctl --user status odsc    - Check daemon status"
echo ""
echo "For more information, see README.md"
