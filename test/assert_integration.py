#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test d'intégration : exécute le VRAI code du plugin (TcpSink + setup_channel)
contre un node Meshtastic simulé (`meshtasticd -s`), sans matériel. Valide le
protocole réel : télémétrie EnvironmentMetrics, texte sur un canal, et création
d'un canal dédié. Sort 0 si tout passe, 1 sinon."""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin", "user"))
import meshtastic_weather as mw  # noqa: E402

HOST = os.environ.get("MESHTASTICD_HOST", "node")
PORT = int(os.environ.get("MESHTASTICD_PORT", "4403"))


def wait_for_port(host, port, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"node {host}:{port} injoignable")


def _channel_names(host):
    import meshtastic.tcp_interface

    iface = meshtastic.tcp_interface.TCPInterface(hostname=host)
    try:
        node = iface.getNode("^local")
        return [c.settings.name for c in node.channels]
    finally:
        iface.close()


def main():
    wait_for_port(HOST, PORT)

    # 1) Envois réels via le sink du plugin.
    sink = mw.TcpSink(HOST)
    assert sink.my_num, "my_num vide"
    n = mw.normalize({
        "usUnits": 1, "dateTime": int(time.time()), "outTemp": 71.6,
        "outHumidity": 60, "barometer": 30.1, "windSpeed": 8, "windDir": 120,
        "windGust": 14, "rainRate": 0.0,
    })
    sink.send_env(n)
    sink.send_text("Integration " + mw.format_summary(n, "Sim"), 0)
    sink.close()
    print(f"OK: télémétrie + texte acceptés par le node (my_num={sink.my_num})")

    # 2) Création d'un canal dédié + vérification côté node.
    index, _psk, _url = mw.setup_channel(HOST, "meteo")
    assert isinstance(index, int) and index >= 1, "index de canal invalide"
    time.sleep(3)
    wait_for_port(HOST, PORT)
    names = _channel_names(HOST)
    assert "meteo" in names, f"canal 'meteo' absent : {names!r}"
    print(f"OK: canal 'meteo' créé (index={index}), canaux={names!r}")

    print("INTEGRATION OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
