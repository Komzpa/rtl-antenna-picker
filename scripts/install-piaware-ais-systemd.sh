#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

if [[ ${EUID} -ne 0 ]]; then
  exec sudo --preserve-env=PATH,RTL_ANTENNA_PICKER_BIN "$0" "$@"
fi

picker_bin=${RTL_ANTENNA_PICKER_BIN:-}
if [[ -z "$picker_bin" ]]; then
  picker_bin=$(command -v rtl-antenna-picker || true)
fi
if [[ -z "$picker_bin" ]]; then
  picker_bin=/usr/local/sbin/rtl-antenna-picker
  install -m 0755 "$repo_root/rtl_antenna_picker.py" "$picker_bin"
fi
case "$picker_bin" in
  /*) ;;
  *) echo "RTL_ANTENNA_PICKER_BIN must be an absolute path, got: $picker_bin" >&2; exit 2 ;;
esac

install_template() {
  local src=$1
  local dst=$2
  install -d -m 0755 "$(dirname -- "$dst")"
  sed "s|@RTL_ANTENNA_PICKER@|$picker_bin|g" "$src" > "$dst.tmp"
  install -m 0644 "$dst.tmp" "$dst"
  rm -f "$dst.tmp"
}

install -d -m 0755 /var/lib/rtl-antenna-picker
install_template "$repo_root/systemd/piaware/rtl-antenna-picker.service" /etc/systemd/system/rtl-antenna-picker.service
install_template "$repo_root/systemd/piaware/dump1090-fa.service.d/10-rtl-antenna-picker.conf" /etc/systemd/system/dump1090-fa.service.d/10-rtl-antenna-picker.conf

if [[ ! -e /etc/systemd/system/ais-rtl-tcp.service && ! -e /lib/systemd/system/ais-rtl-tcp.service ]]; then
  install_template "$repo_root/systemd/piaware/ais-rtl-tcp.service" /etc/systemd/system/ais-rtl-tcp.service
else
  install_template "$repo_root/systemd/piaware/ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf" /etc/systemd/system/ais-rtl-tcp.service.d/10-rtl-antenna-picker.conf
fi

systemctl daemon-reload
systemctl enable rtl-antenna-picker.service >/dev/null

echo "Installed rtl-antenna-picker systemd files using $picker_bin."
echo "Read docs/piaware-ais-systemd.md before the first live cutover."
