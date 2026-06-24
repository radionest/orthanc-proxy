#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

VER="1.12.11"
DICOMWEB_VER="1.23"
DEST="${DEST:-/opt/orthanc}"
BASE="https://orthanc.uclouvain.be/downloads/linux-standard-base"

py_subdir() {
  local id variant ver_id codename
  # shellcheck disable=SC1091
  . "${OS_RELEASE:-/etc/os-release}"
  id="${ID:-}"; variant="${VARIANT_ID:-}"; ver_id="${VERSION_ID:-}"
  codename="${VERSION_CODENAME:-unknown}"

  # Astra Linux SE Smolensk leaves VERSION_CODENAME empty; map VERSION_ID -> Debian base.
  # The plugin embeds its own libpython, so the build is chosen by the OS base (glibc + the
  # base's Python ABI), NOT by the system `python3` (Astra boxes often swap it, e.g. to 3.12).
  if [ "$id" = "astra" ] && [ "$variant" = "smolensk" ]; then
    case "$ver_id" in
      1.8*) echo "debian-bookworm-python-3.11" ;;   # Bookworm, glibc 2.36
      1.7*) echo "debian-buster-python-3.7" ;;       # Buster, glibc 2.28 — only build that runs here
      1.6*) echo "ERROR: Astra 1.6 (Debian 9 Stretch / Python 3.5): no LSB orthanc-python build for" \
                 "Py 3.5. Keep libpython3.7 and use debian-buster-python-3.7, or build from source." >&2
            return 1 ;;
      *)    echo "ERROR: unsupported Astra VERSION_ID '$ver_id'." >&2; return 1 ;;
    esac
    return 0
  fi

  case "$codename" in
    bookworm) echo "debian-bookworm-python-3.11" ;;
    bullseye) echo "debian-bullseye-python-3.9" ;;
    buster)   echo "debian-buster-python-3.7" ;;
    trixie)   echo "debian-trixie-python-3.13" ;;
    *) echo "ERROR: unknown distro 'codename=$codename id=$id variant=$variant ver=$ver_id'; pick the" \
            "matching orthanc-python LSB subdir manually from $BASE/orthanc-python/" >&2; return 1 ;;
  esac
}

# Warn if the libpython the chosen plugin build links against is absent (e.g. an Astra box whose
# system python3 was swapped to 3.12 but libpython3.7 was removed) — the plugin would fail to load.
check_libpython() {
  local need_py
  need_py="$(printf '%s' "$1" | sed -n 's/.*python-\([0-9.]*\)$/\1/p')"
  [ -n "$need_py" ] || return 0
  if ! ldconfig -p 2>/dev/null | grep -q "libpython${need_py}"; then
    echo "WARNING: libpython${need_py} not found (ldconfig) — the Orthanc Python plugin will not" \
         "load. Install it, e.g. 'apt-get install libpython${need_py}', alongside any other Python." >&2
  fi
}

main() {
  local sub py_url orthanc_url dicomweb_url
  sub="$(py_subdir)"
  check_libpython "$sub"
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
  install -m 0644 "$REPO_ROOT/plugin/clarinet_proxy.py" "$REPO_ROOT/plugin/proxy_core.py" "$DEST/plugins/"
  install -m 0755 "$REPO_ROOT/deploy/evict.py" "$DEST/deploy/"
  echo "Installed to $DEST. Place etc/*.json in /etc/orthanc-proxy/ and enable the systemd units."
}

main "$@"
