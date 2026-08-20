#!/bin/sh
# termipdf-wrapper — Launcher for the TermiPDF snap.
#
# The snap ships TermiPDF's `src/` tree under
#     $SNAP/usr/lib/python3/dist-packages/src/
# and we drop it on PYTHONPATH so `python3 -m src.main` resolves
# the `main_window` + `shared` + `features` packages.
#
# This file lives at snap/local/launcher.sh in the source repo and
# is installed as $SNAP/usr/bin/termipdf-wrapper at build time by
# snap/snapcraft.yaml.

set -e

# Snap-specific env: $SNAP is the squashfs mount, $SNAP_USER_DATA is
# writable, $SNAP_USER_COMMON is shared across revisions.
SNAP_PY="$SNAP/usr/lib/python3/dist-packages"

if [ -z "${PYTHONPATH:-}" ]; then
    PYTHONPATH="$SNAP_PY"
else
    PYTHONPATH="$SNAP_PY:$PYTHONPATH"
fi
export PYTHONPATH

# Pick up the host's Qt theme (Adwaita on GNOME, Breeze on KDE, etc.)
# via the gnome content snap's platform plugin.
export QT_QPA_PLATFORMTHEME="${QT_QPA_PLATFORMTHEME:-gnome}"

# Honour the user's locale so translated UI strings (when added) work.
unset LC_ALL
export LANG="${LANG:-C.UTF-8}"

# Run the app. Pass through any args the user supplied (e.g.
# `termipdf my.pdf` to open on launch — feature parity with
# `python src/main.py my.pdf`).
exec python3 -m src.main "$@"