#!/bin/bash
# Helper script to refresh GNOME application menu for ODSC

echo "========================================"
echo "ODSC Desktop Entry Refresh"
echo "========================================"
echo ""

# Update desktop database
echo "Updating desktop database..."
update-desktop-database ~/.local/share/applications 2>/dev/null
echo "✓ Done"

# Update icon cache
echo "Updating icon cache..."
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null
echo "✓ Done"

# Touch the desktop file to update timestamp
echo "Refreshing desktop file timestamp..."
touch ~/.local/share/applications/com.github.odsc.desktop 2>/dev/null
echo "✓ Done"

echo ""
echo "========================================"
echo "Verification"
echo "========================================"

# Verify installation
if [ -f ~/.local/share/applications/com.github.odsc.desktop ]; then
    echo "✓ Desktop file exists"
else
    echo "✗ Desktop file not found!"
    echo ""
    echo "Please run './install.sh' to install the application."
    exit 1
fi

if command -v odsc-gui &> /dev/null; then
    echo "✓ odsc-gui executable found"
else
    echo "✗ odsc-gui not found in PATH!"
    echo ""
    echo "Please run './install.sh' to install the application."
    exit 1
fi

if command -v desktop-file-validate &> /dev/null; then
    if desktop-file-validate ~/.local/share/applications/com.github.odsc.desktop 2>&1 | grep -qi error; then
        echo "⚠ Desktop file has validation errors"
    else
        echo "✓ Desktop file is valid"
    fi
fi

echo ""
echo "========================================"
echo "Next Steps"
echo "========================================"
echo ""

if [ "$XDG_SESSION_TYPE" = "wayland" ]; then
    echo "You're using Wayland. To see the app in your application menu:"
    echo ""
    echo "  1. Press the Super key (Windows key) to open Activities"
    echo "  2. Type 'OneDrive' in the search box"
    echo "  3. The 'OneDrive Sync Client' should appear"
    echo ""
    echo "If it doesn't appear:"
    echo "  • Log out and log back in"
    echo "  • Or run: odsc-gui (to launch directly)"
elif [ "$XDG_CURRENT_DESKTOP" = "ubuntu:GNOME" ] || [ "$XDG_CURRENT_DESKTOP" = "GNOME" ]; then
    echo "To see the app in your application menu:"
    echo ""
    echo "  Option 1: Restart GNOME Shell"
    echo "    • Press Alt+F2"
    echo "    • Type: r"
    echo "    • Press Enter"
    echo ""
    echo "  Option 2: Search for the application"
    echo "    • Press Super key (Windows key)"
    echo "    • Type 'OneDrive'"
    echo ""
    echo "  Option 3: Log out and log back in"
else
    echo "To see the app in your application menu:"
    echo "  • Log out and log back in"
    echo "  • Or search for 'OneDrive' in your application menu"
fi

echo ""
echo "You can also launch directly from terminal:"
echo "  $ odsc-gui"
echo ""
