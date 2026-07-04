#!/bin/sh
# Prépare la config puis lance WeeWX. Node & canal via $NODE_HOST / $CHANNEL_INDEX
# (docker run -e ...), sans rebuild.
set -e
CONF=/data/weewx.conf

# 1) Draine /dev/log : le handler syslog par défaut de WeeWX s'instancie alors
#    sans erreur dans le conteneur (sinon dictConfig casse toute la journalisation).
python3 -c "
import socket, os
try:
    os.unlink('/dev/log')
except OSError:
    pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.bind('/dev/log')
while True:
    try:
        s.recv(65536)
    except OSError:
        break
" &
sleep 0.5

# 2) Journalisation WeeWX vers stdout (racine -> console).
if ! grep -q '^\[Logging\]' "$CONF"; then
  cat >> "$CONF" <<'EOF'

[Logging]
    [[root]]
        handlers = console,
EOF
fi

# 3) Section runtime de l'extension.
if ! grep -q '^\[MeshtasticWeather\]' "$CONF"; then
  cat >> "$CONF" <<EOF

[MeshtasticWeather]
    transport = tcp
    host = ${NODE_HOST}
    channel_index = ${CHANNEL_INDEX}
    station_id = ${STATION_ID:-SIMDOCK}
    telemetry_interval = ${TELEMETRY_INTERVAL:-1}
    text_interval = ${TEXT_INTERVAL:-1}
    dm_enabled = ${DM_ENABLED:-false}
    dry_run = false
EOF
fi

echo "[entrypoint] WeeWX Simulator -> node ${NODE_HOST}, canal ${CHANNEL_INDEX} (archive 60 s)"
exec weewxd /data/weewx.conf
