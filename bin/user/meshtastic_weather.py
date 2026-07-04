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
        # Cumuls de pluie : renseignés par le service (nécessitent l'historique DB).
        "rain_1h": None,
        "rain_24h": None,
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
    if n["rain_1h"] is not None:
        parts.append(f"pluie1h {n['rain_1h']:.1f}mm")
    if n["rain_24h"] is not None:
        parts.append(f"pluie24h {n['rain_24h']:.1f}mm")
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
        h1 = "—" if n["rain_1h"] is None else f"{n['rain_1h']:.1f} mm"
        h24 = "—" if n["rain_24h"] is None else f"{n['rain_24h']:.1f} mm"
        return f"Pluie : 1h {h1} · 24h {h24}"
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


def _default_ble_factory(address):  # pragma: no cover - matériel BLE requis
    from meshtastic.ble_interface import BLEInterface

    return BLEInterface(address=address)


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


class _Sink:
    """Envoi via une interface meshtastic (TCP, BLE…). Les sous-classes ne
    diffèrent que par la CRÉATION de l'interface — le reste est commun."""

    def __init__(self, iface, warmup=3.0):
        self._iface = iface
        self.my_num = iface.myInfo.my_node_num
        # Le node PERD le premier paquet émis juste après connexion : on laisse la
        # liaison se stabiliser avant tout envoi (sinon la télémétrie, 1er envoi
        # de l'archive, ne partirait pas). Réglable via `connect_warmup`.
        time.sleep(warmup)

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


class TcpSink(_Sink):
    """Node joignable en TCP (WiFi)."""

    def __init__(self, host, interface_factory=None, warmup=3.0):
        super().__init__((interface_factory or _default_tcp_factory)(host), warmup)


class BleSink(_Sink):
    """Node joignable en Bluetooth LE (nodes BT-only). Nécessite `bleak` + un BT
    accessible NATIVEMENT (pas dans Docker sur macOS). `address` = MAC/nom/UUID BLE."""

    def __init__(self, address, interface_factory=None, warmup=3.0):
        super().__init__((interface_factory or _default_ble_factory)(address), warmup)


def make_sink(cfg, interface_factory=None):
    """Fabrique le sink selon `transport` (tcp/ble ; serial à venir). Repli en
    FakeSink si dry-run, transport inconnu, ou node injoignable."""
    if as_bool(cfg.get("dry_run"), False):
        return FakeSink(cfg.get("dry_run_log"))
    transport = str(cfg.get("transport", "tcp")).strip().lower()
    warmup = float(cfg.get("connect_warmup", 3))
    builders = {
        "tcp": lambda: TcpSink(cfg["host"], interface_factory, warmup),
        "ble": lambda: BleSink(cfg["ble_address"], interface_factory, warmup),
    }
    build = builders.get(transport)
    if build is None:
        log.warning("transport '%s' non supporté (serial à venir) → dry-run", transport)
        return FakeSink(cfg.get("dry_run_log"))
    try:
        return build()
    except Exception as exc:  # lib absente, adresse manquante, injoignable…
        log.error("Meshtastic injoignable (%s) → dry-run", exc)
        return FakeSink(cfg.get("dry_run_log"))


def _subscribe(callback):
    from pubsub import pub

    pub.subscribe(callback, "meshtastic.receive.text")


def _quiet_lib_logs():
    """Tait le bruit de la lib meshtastic (mort des connexions oisives / broken
    pipe) que NOUS gérons déjà par reconnexion — nos propres logs suffisent."""
    logging.getLogger("meshtastic").setLevel(logging.CRITICAL)


# --------------------------------------------------------------------------- #
# Service WeeWX
# --------------------------------------------------------------------------- #
class MeshtasticWeather(StdService):
    def __init__(self, engine, config_dict, sink=None):
        super().__init__(engine, config_dict)
        cfg = dict(config_dict.get("MeshtasticWeather", {}))
        self._cfg = cfg
        self.station_id = cfg.get("station_id", "")
        self.channel_index = int(cfg.get("channel_index", 0))
        # Cadences en nb d'ARCHIVES : télémétrie toutes les N (défaut 1 = chaque
        # archive ; 0 = jamais) ; texte toutes les M (défaut 0 = jamais, car le
        # canal meteo est partagé entre plusieurs stations).
        self.telemetry_interval = int(cfg.get("telemetry_interval", 1))
        self.text_interval = int(cfg.get("text_interval", 0))
        self.dm_enabled = as_bool(cfg.get("dm_enabled"), False)
        if as_bool(cfg.get("quiet_lib_logs"), True):
            _quiet_lib_logs()
        self.latest = None
        self._n = 0  # compteur d'archives
        # AUCUNE connexion au démarrage : une connexion oisive meurt et son reliquat
        # côté node fait échouer (broken pipe) la reconnexion suivante. On ouvre
        # donc au moment d'émettre (cf. new_archive_record/_reconnect).
        self.sink = sink
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)
        log.info(
            "MeshtasticWeather %s prêt (télémétrie/%s archives, texte/%s, dm=%s)",
            VERSION, self.telemetry_interval, self.text_interval, self.dm_enabled,
        )

    def _due(self, interval):
        return interval > 0 and self._n % interval == 0

    def _rain_totals(self):
        """Cumuls de pluie 1 h / 24 h (mm) depuis la base WeeWX ; (None, None) si
        indisponible (pas de base d'historique)."""
        try:
            dbm = self.engine.db_binder.get_manager()
            now = int(self.latest["time"])
            unit = weewx.units.getStandardUnitType(dbm.std_unit_system, "rain")

            def mm(seconds):
                row = dbm.getSql(
                    f"SELECT SUM(rain) FROM {dbm.table_name} "
                    "WHERE dateTime > ? AND dateTime <= ?",
                    (now - seconds, now),
                )
                raw = row[0] if row and row[0] is not None else None
                if raw is None:
                    return None
                return weewx.units.convert(weewx.units.ValueTuple(raw, *unit), "mm")[0]

            return mm(3600), mm(86400)
        except Exception as exc:
            log.debug("cumuls pluie indisponibles : %s", exc)
            return None, None

    def new_archive_record(self, event):
        self._n += 1
        self.latest = normalize(event.record)
        send_tel = self._due(self.telemetry_interval)
        send_txt = self._due(self.text_interval)
        # Rien à émettre cette archive (et pas de bot DM à alimenter) -> pas de paquet.
        if not (send_tel or send_txt or self.dm_enabled):
            return
        self.latest["rain_1h"], self.latest["rain_24h"] = self._rain_totals()
        # Connexion FRAÎCHE à chaque archive : le node ferme les connexions TCP
        # inactives entre deux envois — une connexion persistante serait morte.
        self._reconnect()
        try:
            kinds = []
            if send_tel:
                self.sink.send_env(self.latest)
                kinds.append("télémétrie")
            if send_txt:
                text = format_summary(self.latest, self.station_id)
                self.sink.send_text(text, self.channel_index)
                kinds.append("texte")
            if kinds:
                log.info("archive #%s → %s envoyé (%s, canal %s)",
                         self._n, " + ".join(kinds),
                         type(self.sink).__name__, self.channel_index)
        except Exception as exc:
            log.error("envoi échoué : %s", exc)
        finally:
            # Sans DM : ouvrir/envoyer/FERMER — aucune connexion oisive, donc pas
            # de « reader timed out » toutes les minutes. Avec DM : on garde la
            # connexion ouverte pour écouter (au prix de ce bruit de lecteur).
            if not self.dm_enabled:
                self._close_current()

    def _close_current(self):
        try:
            if self.sink is not None:
                self.sink.close()
        except Exception:
            pass
        self.sink = None

    def _reconnect(self):
        self._close_current()
        self.sink = make_sink(self._cfg)
        if self.dm_enabled and not isinstance(self.sink, FakeSink):
            _subscribe(self._on_receive)

    def _on_receive(self, packet, interface):
        try:
            if packet.get("to") != self.sink.my_num:
                return
            decoded = packet.get("decoded") or {}
            if decoded.get("portnum") != "TEXT_MESSAGE_APP":
                return
            answer = reply(command(decoded.get("text", "")), self.latest, self.station_id)
            self.sink.send_dm(answer, packet.get("from"))
        except Exception as exc:
            log.error("DM ignoré : %s", exc)

    def shutDown(self):
        self._close_current()


# --------------------------------------------------------------------------- #
# CLI : création du canal dédié via TCP (opt-in ; ne touche pas aux existants)
# --------------------------------------------------------------------------- #
def channel_url(settings, lora_config):
    """URL partageable (meshtastic.org/e/#…) d'UN canal — pour QR/lien d'ajout.
    N'encode que ce canal (+ la config LoRa), pas les autres canaux du node."""
    from meshtastic.protobuf import apponly_pb2

    cs = apponly_pb2.ChannelSet()
    cs.settings.append(settings)
    cs.lora_config.CopyFrom(lora_config)
    raw = base64.urlsafe_b64encode(cs.SerializeToString()).decode("ascii")
    raw = raw.replace("=", "").replace("+", "-").replace("/", "_")
    return "https://meshtastic.org/e/#" + raw


def _safe_close(iface):
    try:
        iface.close()
    except Exception:
        pass


def _channel_name_at(host, index, factory):
    """Relit (connexion fraîche) le nom du canal `index` pour confirmer l'écriture."""
    iface = factory(host)
    try:
        return iface.getNode("^local").channels[index].settings.name
    finally:
        _safe_close(iface)


def setup_channel(host, name, psk=None, interface_factory=None, attempts=6, delay=4):
    """Ajoute un canal SECONDARY dans un slot libre, sans toucher aux existants.
    Le node peut couper la connexion pendant l'écriture admin (broken pipe) →
    on RÉESSAIE jusqu'à confirmation par relecture après reconnexion. Retourne
    (index, psk_b64, url_partageable)."""
    from meshtastic.protobuf import channel_pb2

    factory = interface_factory or _default_tcp_factory
    key = decode_psk(psk)
    for _ in range(attempts):
        iface = factory(host)
        try:
            node = iface.getNode("^local")
            ch = node.getDisabledChannel()
            if ch is None:
                raise RuntimeError("aucun canal libre sur le node")
            index = ch.index
            ch.settings.name = name
            ch.settings.psk = key
            ch.role = channel_pb2.Channel.Role.SECONDARY
            node.writeChannel(index)
            url = channel_url(ch.settings, node.localConfig.lora)
        finally:
            _safe_close(iface)
        time.sleep(delay)
        if _channel_name_at(host, index, factory) == name:
            return index, base64.b64encode(key).decode("ascii"), url
    raise RuntimeError(f"écriture du canal non confirmée après {attempts} essais")


def print_qr(url, out=None):
    """Affiche un QR ASCII de `url` si la lib `qrcode` est là, sinon un rappel."""
    stream = out or sys.stdout
    try:
        import qrcode
    except ImportError:
        stream.write("(pip install qrcode pour afficher le QR dans le terminal)\n")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make()
    qr.print_ascii(out=stream, invert=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="meshtastic_weather", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sc = sub.add_parser("setup-channel", help="créer un canal dédié via TCP")
    sc.add_argument("--host", required=True)
    sc.add_argument("--name", default="meteo")
    sc.add_argument("--psk", default="random")
    args = parser.parse_args(argv)
    if args.cmd == "setup-channel":
        index, psk_b64, url = setup_channel(args.host, args.name, args.psk)
        print(f"Canal '{args.name}' créé : index={index}")
        print(f"PSK (base64) : {psk_b64}")
        print(f"Lien/QR      : {url}")
        print_qr(url)
        print(f"→ mettre channel_index = {index} dans [MeshtasticWeather].")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
