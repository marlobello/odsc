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
    sudo apt-get update
    sudo apt-get install -y \
        python3-pip \
        python3-gi \
        python3-gi-cairo \
        gir1.2-gtk-3.0 \
        python3-dbus \
        python3-requests \
        python3-watchdog \
        python3-dateutil
    echo "✓ System dependencies and Python packages installed"
elif command -v dnf &> /dev/null; then
    sudo dnf install -y \
        python3-pip \
        python3-gobject \
        gtk3 \
        python3-dbus \
        python3-requests \
        python3-watchdog \
        python3-dateutil
    echo "✓ System dependencies and Python packages installed"
else
    echo "Warning: Could not detect package manager. Please install dependencies manually."
fi

# Install ODSC package (setup.py only installs entry points, not dependencies)
echo ""
echo "Installing ODSC entry points..."
pip3 install --user --break-system-packages -e . --no-deps
echo "✓ ODSC installed"

# Create sync directory
echo ""
echo "Creating default sync directory..."
mkdir -p "$HOME/OneDrive"
echo "✓ Sync directory created at $HOME/OneDrive"

# Install icon
echo ""
echo "Installing application icon..."
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
cp desktop/odsc.svg "$HOME/.local/share/icons/hicolor/scalable/apps/"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "✓ Application icon installed"

# Install systemd service (optional)
echo ""
read -p "Do you want to install the systemd service? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/.config/systemd/user"
    cp systemd/odsc.service "$HOME/.config/systemd/user/"
    systemctl --user daemon-reload
    echo "✓ Systemd service installed"
    echo ""
    echo "To enable auto-start on login:"
    echo "  systemctl --user enable odsc"
    echo "To start the service now:"
    echo "  systemctl --user start odsc"
fi

# Install desktop entry (optional)
echo ""
read -p "Do you want to install the desktop application entry? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/.local/share/applications"
    # Install with application ID name for proper GNOME integration
    cp desktop/odsc.desktop "$HOME/.local/share/applications/com.github.odsc.desktop"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo "✓ Desktop entry installed"
fi

echo ""
echo "==================================="
echo "Installation completed successfully!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. Launch ODSC GUI from your applications menu or run: odsc-gui"
echo "2. Click 'Authenticate' to log in with your Microsoft account"
echo "3. Start syncing!"
echo ""
echo "For more information, see README.md"
