#!/bin/sh
# Install fsd-toggle units as real files under /etc (not symlinks into /data).
# Run on the comma:  sudo sh /data/scripts/install-fsd-systemd.sh
set -e
SRC="${1:-/data}"
SUDO="${SUDO:-sudo}"

$SUDO mount -o remount,rw /

for name in fsd-toggle.service fsd-toggle.timer; do
  if [ ! -f "$SRC/$name" ]; then
    echo "Missing $SRC/$name" >&2
    exit 1
  fi
  $SUDO rm -f "/etc/systemd/system/$name"
  $SUDO cp "$SRC/$name" "/etc/systemd/system/$name"
done

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now fsd-toggle.service
$SUDO systemctl enable --now fsd-toggle.timer

$SUDO mount -o remount,ro /

echo "Installed real unit files under /etc/systemd/system/ (no /data symlinks)."
echo "Check: ls -la /etc/systemd/system/fsd-toggle.service"
echo "Reboot to verify auto-start."
