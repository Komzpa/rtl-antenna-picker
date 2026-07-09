#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

if [[ ${EUID} -ne 0 ]]; then
  exec sudo --preserve-env=PATH "$0" "$@"
fi

install -m 0755 "$repo_root/rtl_antenna_picker.py" /usr/local/sbin/rtl-antenna-picker
install -d -m 0755 /var/lib/rtl-antenna-picker
install -m 0644 "$repo_root/systemd/piaware/rtl-antenna-picker.service" /etc/systemd/system/rtl-antenna-picker.service
install -d -m 0755 /etc/systemd/system/dump1090-fa.service.d
install -m 0644 "$repo_root/systemd/piaware/dump1090-fa.service.d/10-rtl-antenna-picker.conf" /etc/systemd/system/dump1090-fa.service.d/10-rtl-antenna-picker.conf

if [[ ! -e /etc/systemd/system/ais-rtl-tcp.service && ! -e /lib/systemd/system/ais-rtl-tcp.service ]]; then
  install -m 0644 "$repo_root/systemd/piaware/ais-rtl-tcp.service" /etc/systemd/system/ais-rtl-tcp.service
else
  install -d -m 0755 /etc/systemd/system/ais-rtl-tcp.service.d
  install -m 0644 "$repo_root/systemd/piaware/ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf" /etc/systemd/system/ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf
fi

systemctl daemon-reload
systemctl enable rtl-antenna-picker.service >/dev/null

echo "Installed rtl-antenna-picker systemd files."
echo "Read docs/piaware-ais-systemd.md before the first live cutover."
