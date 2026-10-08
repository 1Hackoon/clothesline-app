#!/usr/bin/env bash
# Clothesline Uninstaller
set -e

APP_NAME="clothesline"

if [ "$EUID" -eq 0 ]; then
    INSTALL_BIN="/usr/local/bin/${APP_NAME}"
    INSTALL_DIR="/usr/local/share/${APP_NAME}"
    APP_FILE="/usr/share/applications/${APP_NAME}.desktop"
    AUTOSTART_FILE="/etc/xdg/autostart/${APP_NAME}.desktop"
    ICON_FILE="/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"
    echo "==> Uninstalling Clothesline system-wide..."
else
    INSTALL_BIN="${HOME}/.local/bin/${APP_NAME}"
    INSTALL_DIR="${HOME}/.local/share/${APP_NAME}"
    APP_FILE="${HOME}/.local/share/applications/${APP_NAME}.desktop"
    AUTOSTART_FILE="${HOME}/.config/autostart/${APP_NAME}.desktop"
    ICON_FILE="${HOME}/.local/share/icons/hicolor/256x256/apps/${APP_NAME}.png"
    echo "==> Uninstalling Clothesline for user ${USER}..."
fi

rm -f "${INSTALL_BIN}"
rm -rf "${INSTALL_DIR}"
rm -f "${APP_FILE}"
rm -f "${AUTOSTART_FILE}"
rm -f "${ICON_FILE}"

echo "✓ Clothesline has been cleanly removed."
