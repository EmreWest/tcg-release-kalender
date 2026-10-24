#!/bin/sh
# Startet als root nur so lange, bis das Datenverzeichnis dem App-Benutzer gehört,
# und läuft danach ohne Root-Rechte weiter.
set -e
if [ "$(id -u)" = "0" ]; then
  chown -R app:app /data 2>/dev/null || true
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
