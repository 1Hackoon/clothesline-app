#!/usr/bin/env bash
# Clothesline Installer (Local or System-wide)
set -e

APP_NAME="clothesline"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$EUID" -eq 0 ]; then
    INSTALL_BIN="/usr/local/bin"
    INSTALL_DIR="/usr/local/share/${APP_NAME}"
    APP_DIR="/usr/share/applications"
    AUTOSTART_DIR="/etc/xdg/autostart"
    ICON_DIR="/usr/share/icons/hicolor/256x256/apps"
    echo "==> Installing Clothesline system-wide..."
else
    INSTALL_BIN="${HOME}/.local/bin"
    INSTALL_DIR="${HOME}/.local/share/${APP_NAME}"
    APP_DIR="${HOME}/.local/share/applications"
    AUTOSTART_DIR="${HOME}/.config/autostart"
    ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"
    echo "==> Installing Clothesline for user ${USER}..."
fi

mkdir -p "${INSTALL_BIN}" "${INSTALL_DIR}" "${APP_DIR}" "${AUTOSTART_DIR}" "${ICON_DIR}"

# 1. Copy application files
cp "${SCRIPT_DIR}/clothesline.py" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/editor.py" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/shots.py" "${INSTALL_DIR}/"
chmod 755 "${INSTALL_DIR}/clothesline.py"

# 2. Executable launcher
cat <<EOF > "${INSTALL_BIN}/${APP_NAME}"
#!/bin/sh
exec python3 "${INSTALL_DIR}/clothesline.py" "\$@"
EOF
chmod 755 "${INSTALL_BIN}/${APP_NAME}"

# 3. Application icon
if [ -f "${SCRIPT_DIR}/assets/clothesline.png" ]; then
    cp "${SCRIPT_DIR}/assets/clothesline.png" "${ICON_DIR}/${APP_NAME}.png"
fi

# 4. Desktop entry
cat <<EOF > "${APP_DIR}/${APP_NAME}.desktop"
[Desktop Entry]
Name=Clothesline
Comment=Screenshots hung out to dry
Exec=${INSTALL_BIN}/${APP_NAME}
Icon=${APP_NAME}
Terminal=false
Type=Application
Categories=Utility;Graphics;
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF
chmod 644 "${APP_DIR}/${APP_NAME}.desktop"

# 5. Autostart on login (Permanent)
cp "${APP_DIR}/${APP_NAME}.desktop" "${AUTOSTART_DIR}/${APP_NAME}.desktop"

# 6. Update desktop & icon caches if available
which update-desktop-database >/dev/null 2>&1 && update-desktop-database "${APP_DIR}" || true
which gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$(dirname "$(dirname "$(dirname "${ICON_DIR}")")")" || true

echo "✓ Successfully installed Clothesline!"
echo "  • Executable: ${INSTALL_BIN}/${APP_NAME}"
echo "  • Desktop Launcher: ${APP_DIR}/${APP_NAME}.desktop"
echo "  • Permanent Autostart: ${AUTOSTART_DIR}/${APP_NAME}.desktop"
echo ""
echo "Clothesline will now start automatically whenever you log in."
echo "You can launch it right now by running: ${APP_NAME} &"
