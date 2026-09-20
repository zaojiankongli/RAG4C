#!/bin/sh
set -eu

CONF_D="${CONF_D_DIR:-/etc/postgresql/conf.d}"

if [ -d "$CONF_D" ]; then
  if ! grep -q "include_dir = '${CONF_D}'" "$PGDATA/postgresql.conf"; then
    printf '\ninclude_dir = %s\n' "'${CONF_D}'" >> "$PGDATA/postgresql.conf"
    echo "[init] include_dir '${CONF_D}' appended"
  else
    echo "[init] include_dir already present, skip"
  fi
else
  echo "[init] WARN: ${CONF_D} not found, skip"
fi
