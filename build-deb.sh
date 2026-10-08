#!/usr/bin/env bash
set -e

VERSION="1.0.0"
PKG_NAME="clothesline"
ARCH="all"
BUILD_DIR="build_deb/${PKG_NAME}_${VERSION}_${ARCH}"

rm -rf build_deb
mkdir -p "${BUILD_DIR}/DEBIAN"
mkdir -p "${BUILD_DIR}/usr/bin"
mkdir -p "${BUILD_DIR}/usr/lib/${PKG_NAME}"
mkdir -p "${BUILD_DIR}/usr/share/applications"
mkdir -p "${BUILD_DIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${BUILD_DIR}/usr/share/doc/${PKG_NAME}"
mkdir -p "${BUILD_DIR}/etc/xdg/autostart"

# 1. Control file
cat <<EOC > "${BUILD_DIR}/DEBIAN/control"
Package: ${PKG_NAME}
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: ${ARCH}
Depends: python3, python3-gi, python3-cairo, python3-pil, python3-tk, wl-clipboard
Maintainer: Clothesline Project <https://github.com/1Hackoon/clothesline-app>
Description: Screenshots hung out to dry
 A minimalist desktop overlay for Linux and Windows that hangs your screenshots
 on a line at the top of your screen with wooden clothespins.
EOC

# 2. Postinst
cat << "EOP" > "${BUILD_DIR}/DEBIAN/postinst"
#!/bin/sh
set -e
if which update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if which gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
EOP
chmod 755 "${BUILD_DIR}/DEBIAN/postinst"

# 3. Postrm
cat << "EOR" > "${BUILD_DIR}/DEBIAN/postrm"
#!/bin/sh
set -e
if which update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if which gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
EOR
chmod 755 "${BUILD_DIR}/DEBIAN/postrm"

# 4. Copy app files
cp clothesline.py "${BUILD_DIR}/usr/lib/${PKG_NAME}/"
cp editor.py "${BUILD_DIR}/usr/lib/${PKG_NAME}/"
cp shots.py "${BUILD_DIR}/usr/lib/${PKG_NAME}/"
chmod 755 "${BUILD_DIR}/usr/lib/${PKG_NAME}/clothesline.py"

# 5. Executable launcher
cat << "EOX" > "${BUILD_DIR}/usr/bin/clothesline"
#!/bin/sh
exec /usr/bin/python3 /usr/lib/clothesline/clothesline.py "$@"
EOX
chmod 755 "${BUILD_DIR}/usr/bin/clothesline"

# 6. Desktop entry & autostart
cat << "EOD" > "${BUILD_DIR}/usr/share/applications/clothesline.desktop"
[Desktop Entry]
Name=Clothesline
Comment=Screenshots hung out to dry
Exec=/usr/bin/clothesline
Icon=clothesline
Terminal=false
Type=Application
Categories=Utility;Graphics;
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOD
chmod 644 "${BUILD_DIR}/usr/share/applications/clothesline.desktop"
cp "${BUILD_DIR}/usr/share/applications/clothesline.desktop" "${BUILD_DIR}/etc/xdg/autostart/clothesline.desktop"

# 7. Icon and documentation
cp assets/clothesline.png "${BUILD_DIR}/usr/share/icons/hicolor/256x256/apps/clothesline.png"
cp README.md "${BUILD_DIR}/usr/share/doc/${PKG_NAME}/"
cp LICENSE "${BUILD_DIR}/usr/share/doc/${PKG_NAME}/copyright"

# 8. Build package
dpkg-deb --build --root-owner-group "${BUILD_DIR}" "${PKG_NAME}_${VERSION}_${ARCH}.deb"
rm -rf build_deb
echo "Successfully built ${PKG_NAME}_${VERSION}_${ARCH}.deb!"
