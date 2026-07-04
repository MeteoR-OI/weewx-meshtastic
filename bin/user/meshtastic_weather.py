# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MeteoR-OI
"""WeeWX → Meshtastic : pousse la météo (télémétrie EnvironmentMetrics + résumé
texte sur un canal + bot d'interrogation en DM) vers un node Meshtastic.

Une seule `StdService` détient une connexion persistante au node (un seul client
TCP). Le transport est *pluggable* (`tcp` aujourd'hui ; `serial`/`ble` = phases
futures, même API `MeshtasticSink`). Tout l'I/O node est injectable pour des
tests 100 % sans matériel ; en Docker on valide le vrai protocole contre
`meshtasticd -s`.

Cible : WeeWX 4/5 (Python 3). La lib `meshtastic` (Python 3) est importée
paresseusement ; absente/injoignable → repli automatique en dry-run (`FakeSink`).
"""
import argparse
import base64
import json
import logging
import sys
import time

import weewx
import weewx.units
from weewx.engine import StdService

log = logging.getLogger(__name__)

VERSION = "0.1.0"
MAX_TEXT = 200  # marge sûre sous la charge utile LoRa (~237 o)

# Conversions unité-cible par observation (via weewx.units, gère US et métrique).
_TARGET = {
    "outTemp": "degree_C",
    "barometer": "mbar",
    "windSpeed": "meter_per_second",
    "windGust": "meter_per_second",
    "rainRate": "mm_per_hour",
    "dayRain": "mm",
}
_CARDINALS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO",
]


# --------------------------------------------------------------------------- #
# Helpers purs (aucune dépendance node — testés à 100 %)
# --------------------------------------------------------------------------- #
def as_bool(value, default=False):
    """Coerce une valeur de config WeeWX (souvent 'true'/'false') en bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _num(value):
    """float(value) ou None (valeur absente/vide)."""
    if value is None or value == "":
        return None
    return float(value)


def _conv(record, obs):
    """Valeur métrique de `obs` dans un enregistrement WeeWX, ou None si absente."""
    if record.get(obs) is None:
        return None
    vt = weewx.units.as_value_tuple(record, obs)
    return weewx.units.convert(vt, _TARGET[obs])[0]


def normalize(record):
    """Enregistrement ARCHIVE WeeWX → dict métrique homogène."""
    return {
        "time": int(record.get("dateTime") or 0),
        "temp_c": _conv(record, "outTemp"),
        "humidity": _num(record.get("outHumidity")),
        "pressure_hpa": _conv(record, "barometer"),
        "wind_ms": _conv(record, "windSpeed"),
        "wind_dir": _num(record.get("windDir")),
        "gust_ms": _conv(record, "windGust"),
        "rain_rate_mmh": _conv(record, "rainRate"),
        "rain_day_mm": _conv(record, "dayRain"),
    }


def cardinal(deg):
    """Degrés → rose des vents 16 points (None si direction inconnue)."""
    if deg is None:
        return None
    return _CARDINALS[int((deg % 360) / 22.5 + 0.5) % 16]


def format_summary(n, station=""):
    """Résumé texte compact (≤ MAX_TEXT) d'un dict normalisé."""
    parts = []
    if n["temp_c"] is not None:
        parts.append("{:.1f}°C".format(n["temp_c"]))
    if n["humidity"] is not None:
        parts.append("{:.0f}%HR".format(n["humidity"]))
    if n["pressure_hpa"] is not None:
        parts.append("{:.0f}hPa".format(n["pressure_hpa"]))
    if n["wind_ms"] is not None:
        card = cardinal(n["wind_dir"])
        suffix = " " + card if card else ""
        parts.append("vent {:.0f}km/h{}".format(n["wind_ms"] * 3.6, suffix))
    if n["rain_rate_mmh"] is not None:
        parts.append("pluie {:.1f}mm/h".format(n["rain_rate_mmh"]))
    body = " · ".join(parts) if parts else "pas de données"
    head = station + " — " if station else ""
    return (head + body)[:MAX_TEXT]


def command(text):
    """Texte reçu en DM → commande normalisée."""
    t = (text or "").strip().lower()
    if not t:
        return "help"
    if t in ("météo", "meteo", "now"):
        return "now"
    if t.startswith("vent"):
        return "wind"
    if t.startswith("pluie"):
        return "rain"
    if t.startswith("temp"):
        return "temp"
    return "help"


def _help_text():
    return "Commandes : météo, vent, pluie, temp, aide."


def reply(cmd, n, station=""):
    """Réponse texte à une commande DM (n = dernier relevé normalisé ou None)."""
    if n is None:
        return "Aucune donnée météo pour l'instant."
    if cmd == "now":
        return format_summary(n, station)
    if cmd == "wind":
        if n["wind_ms"] is None:
            return "Vent : —"
        card = cardinal(n["wind_dir"])
        gust = "" if n["gust_ms"] is None else " (raf. {:.0f})".format(n["gust_ms"] * 3.6)
        return "Vent : {:.0f} km/h {}{}".format(n["wind_ms"] * 3.6, card or "?", gust)
    if cmd == "rain":
        rate = "—" if n["rain_rate_mmh"] is None else "{:.1f} mm/h".format(n["rain_rate_mmh"])
        day = "" if n["rain_day_mm"] is None else " · cumul jour {:.1f} mm".format(n["rain_day_mm"])
        return f"Pluie : {rate}{day}"
    if cmd == "temp":
        temp = "—" if n["temp_c"] is None else "{:.1f}°C".format(n["temp_c"])
        hum = "" if n["humidity"] is None else " · {:.0f}%HR".format(n["humidity"])
        return f"Température : {temp}{hum}"
    return _help_text()


def decode_psk(psk):
    """PSK : 'random'/None → clé 256 bits générée ; sinon base64 → octets."""
    if psk is None or psk == "random":
        import meshtastic.util
        return meshtastic.util.genPSK256()
    return base64.b64decode(psk)


# --------------------------------------------------------------------------- #
# Sinks — abstraction I/O node (transport pluggable)
# --------------------------------------------------------------------------- #
def _default_tcp_factory(host):  # pragma: no cover - I/O réel, couvert par Docker
    from meshtastic.tcp_interface import TCPInterface

    return TCPInterface(hostname=host)


class FakeSink:
    """Sink de repli/tests : enregistre les envois (et les logue) sans émettre."""

    my_num = 0

    def __init__(self, log_path=None):
        self.calls = []
        self.log_path = log_path

    def _record(self, kind, payload):
        entry = {"kind": kind, "payload": payload}
        self.calls.append(entry)
        line = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        if self.log_path:
            with open(self.log_path, "a") as fh:
                fh.write(line + "\n")
        else:
            log.info("[dry-run] %s", line)

    def send_env(self, metrics):
        self._record("env", metrics)

    def send_text(self, text, channel_index):
        self._record("text", {"text": text, "channel_index": channel_index})

    def send_dm(self, text, dest):
        self._record("dm", {"text": text, "dest": dest})

    def close(self):
        self._record("close", {})


class TcpSink:
    """Sink réel sur un node Meshtastic joignable en TCP (WiFi)."""

    def __init__(self, host, interface_factory=None):
        factory = interface_factory or _default_tcp_factory
        self._iface = factory(host)
        self.my_num = self._iface.myInfo.my_node_num

    def send_env(self, metrics):
        from meshtastic import BROADCAST_ADDR
        from meshtastic.protobuf import portnums_pb2, telemetry_pb2

        t = telemetry_pb2.Telemetry()
        t.time = metrics.get("time") or int(time.time())
        env = t.environment_metrics
        if metrics.get("temp_c") is not None:
            env.temperature = metrics["temp_c"]
        if metrics.get("humidity") is not None:
            env.relative_humidity = metrics["humidity"]
        if metrics.get("pressure_hpa") is not None:
            env.barometric_pressure = metrics["pressure_hpa"]
        if metrics.get("wind_ms") is not None:
            env.wind_speed = metrics["wind_ms"]
        if metrics.get("wind_dir") is not None:
            env.wind_direction = int(metrics["wind_dir"])
        if metrics.get("gust_ms") is not None:
            env.wind_gust = metrics["gust_ms"]
        self._iface.sendData(
            t,
            destinationId=BROADCAST_ADDR,
            portNum=portnums_pb2.PortNum.TELEMETRY_APP,
            wantResponse=False,
        )

    def send_text(self, text, channel_index):
        self._iface.sendText(text, channelIndex=channel_index)

    def send_dm(self, text, dest):
        self._iface.sendText(text, destinationId=dest)

    def close(self):
        self._iface.close()


def make_sink(cfg, interface_factory=None):
    """Fabrique le sink selon la config. Repli en FakeSink si dry-run, transport
    non encore supporté (serial/ble = phases futures), ou node injoignable."""
    if as_bool(cfg.get("dry_run"), False):
        return FakeSink(cfg.get("dry_run_log"))
    transport = str(cfg.get("transport", "tcp")).strip().lower()
    if transport != "tcp":
        # `serial`/`ble` : point d'extension futur (même interface MeshtasticSink).
        log.warning("transport '%s' non supporté (phase future) → dry-run", transport)
        return FakeSink(cfg.get("dry_run_log"))
    try:
        return TcpSink(cfg["host"], interface_factory=interface_factory)
    except Exception as exc:  # lib absente, connexion impossible…
        log.error("Meshtastic injoignable (%s) → dry-run", exc)
        return FakeSink(cfg.get("dry_run_log"))


def _subscribe(callback):
    from pubsub import pub

    pub.subscribe(callback, "meshtastic.receive.text")


# --------------------------------------------------------------------------- #
# Service WeeWX
# --------------------------------------------------------------------------- #
class MeshtasticWeather(StdService):
    def __init__(self, engine, config_dict, sink=None):
        super().__init__(engine, config_dict)
        cfg = dict(config_dict.get("MeshtasticWeather", {}))
        self._cfg = cfg
        self.station = cfg.get("station_name", "")
        self.channel_index = int(cfg.get("channel_index", 0))
        self.send_telemetry = as_bool(cfg.get("send_telemetry"), True)
        self.send_text = as_bool(cfg.get("send_text"), True)
        self.dm_enabled = as_bool(cfg.get("dm_enabled"), True)
        self.latest = None
        self.sink = sink if sink is not None else make_sink(cfg)
        if self.dm_enabled and not isinstance(self.sink, FakeSink):
            _subscribe(self._on_receive)
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)
        log.info("MeshtasticWeather %s prêt (sink=%s)", VERSION, type(self.sink).__name__)

    def new_archive_record(self, event):
        n = normalize(event.record)
        self.latest = n
        try:
            if self.send_telemetry:
                self.sink.send_env(n)
            if self.send_text:
                self.sink.send_text(format_summary(n, self.station), self.channel_index)
        except Exception as exc:
            log.error("envoi échoué (%s) → reconnexion", exc)
            self._reconnect()

    def _reconnect(self):
        try:
            self.sink.close()
        except Exception:
            pass
        self.sink = make_sink(self._cfg)

    def _on_receive(self, packet, interface):
        try:
            if packet.get("to") != self.sink.my_num:
                return
            decoded = packet.get("decoded") or {}
            if decoded.get("portnum") != "TEXT_MESSAGE_APP":
                return
            answer = reply(command(decoded.get("text", "")), self.latest, self.station)
            self.sink.send_dm(answer, packet.get("from"))
        except Exception as exc:
            log.error("DM ignoré : %s", exc)

    def shutDown(self):
        try:
            self.sink.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# CLI : création du canal dédié via TCP (opt-in ; ne touche pas aux existants)
# --------------------------------------------------------------------------- #
def setup_channel(host, name, psk=None, interface_factory=None):
    """Ajoute un canal SECONDARY dans un slot libre. Retourne (index, psk_b64)."""
    from meshtastic.protobuf import channel_pb2

    factory = interface_factory or _default_tcp_factory
    iface = factory(host)
    try:
        node = iface.getNode("^local")
        ch = node.getDisabledChannel()
        if ch is None:
            raise RuntimeError("aucun canal libre sur le node")
        ch.settings.name = name
        ch.settings.psk = decode_psk(psk)
        ch.role = channel_pb2.Channel.Role.SECONDARY
        node.writeChannel(ch.index)
        return ch.index, base64.b64encode(ch.settings.psk).decode("ascii")
    finally:
        iface.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="meshtastic_weather", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sc = sub.add_parser("setup-channel", help="créer un canal dédié via TCP")
    sc.add_argument("--host", required=True)
    sc.add_argument("--name", default="meteo")
    sc.add_argument("--psk", default="random")
    args = parser.parse_args(argv)
    if args.cmd == "setup-channel":
        index, psk_b64 = setup_channel(args.host, args.name, args.psk)
        print(f"Canal '{args.name}' créé : index={index} psk={psk_b64}")
        print(f"→ mettre channel_index = {index} dans [MeshtasticWeather].")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
