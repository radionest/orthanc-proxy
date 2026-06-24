#!/usr/bin/env bash
set -euo pipefail

VER="1.12.11"
DICOMWEB_VER="1.23"
DEST="${DEST:-/opt/orthanc}"
BASE="https://orthanc.uclouvain.be/downloads/linux-standard-base"

py_subdir() {
  local codename pyver
  codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-unknown}")"
  pyver="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
  case "$codename" in
    bookworm) echo "debian-bookworm-python-3.11" ;;
    bullseye) echo "debian-bullseye-python-3.9" ;;
    trixie)   echo "debian-trixie-python-3.13" ;;
    *) echo "ERROR: unknown distro '$codename' (python $pyver); pick the matching" \
            "orthanc-python LSB subdir manually from $BASE/orthanc-python/" >&2; return 1 ;;
  esac
}

main() {
  local sub py_url orthanc_url dicomweb_url
  sub="$(py_subdir)"
  orthanc_url="$BASE/orthanc/$VER/Orthanc"
  dicomweb_url="$BASE/orthanc-dicomweb/$DICOMWEB_VER/libOrthancDicomWeb.so"
  py_url="$BASE/orthanc-python/$sub/mainline/libOrthancPython.so"

  echo "Orthanc:  $orthanc_url"
  echo "DicomWeb: $dicomweb_url"
  echo "Python:   $py_url"
  if [ "${DRYRUN:-0}" = "1" ]; then return 0; fi

  install -d "$DEST/bin" "$DEST/plugins" "$DEST/deploy"
  curl -fsSL "$orthanc_url"  -o "$DEST/bin/Orthanc"
  curl -fsSL "$dicomweb_url" -o "$DEST/plugins/libOrthancDicomWeb.so"
  curl -fsSL "$py_url"       -o "$DEST/plugins/libOrthancPython.so"
  chmod +x "$DEST/bin/Orthanc"
  install -m 0644 plugin/clarinet_proxy.py plugin/proxy_core.py "$DEST/plugins/"
  install -m 0755 deploy/evict.py "$DEST/deploy/"
  echo "Installed to $DEST. Place etc/*.json in /etc/orthanc-proxy/ and enable the systemd units."
}

main "$@"
