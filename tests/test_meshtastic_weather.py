# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests unitaires — couverture 100 % (statements + branches). meshtastic/pubsub
sont réels (pip) mais toute I/O node est injectée/mockée : aucun matériel requis."""
import base64
import logging
from unittest import mock

import meshtastic_weather as mw
import pytest
import weewx


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _no_sleep():
    # Neutralise les temporisations (warm-up connexion, réessais) pour des tests rapides.
    with mock.patch.object(mw.time, "sleep"):
        yield


US_RECORD = {
    "usUnits": weewx.US, "dateTime": 1000, "outTemp": 68.0, "outHumidity": 55.0,
    "barometer": 30.0, "windSpeed": 10.0, "windGust": 15.0, "windDir": 90.0,
    "rainRate": 0.1, "dayRain": 0.2,
}


def norm(**over):
    base = {
        "time": 1000, "temp_c": 20.0, "humidity": 55.0, "pressure_hpa": 1015.0,
        "wind_ms": 4.47, "wind_dir": 90.0, "gust_ms": 6.7,
        "rain_1h": 1.2, "rain_24h": 8.4,
    }
    base.update(over)
    return base


def fake_iface(node_num=42):
    iface = mock.Mock()
    iface.myInfo.my_node_num = node_num
    return iface


# --------------------------------------------------------------------------- #
# Helpers purs
# --------------------------------------------------------------------------- #
def test_as_bool():
    assert mw.as_bool(None) is False
    assert mw.as_bool(None, True) is True
    assert mw.as_bool(True) is True
    assert mw.as_bool("true") is True
    assert mw.as_bool("false") is False


def test_num():
    assert mw._num(None) is None
    assert mw._num("") is None
    assert mw._num("3.5") == 3.5


def test_normalize_us_and_metric():
    n = mw.normalize(US_RECORD)
    assert n["temp_c"] == pytest.approx(20.0)
    assert n["wind_ms"] == pytest.approx(4.4704)
    assert n["rain_1h"] is None and n["rain_24h"] is None  # renseignés par le service
    empty = mw.normalize({"usUnits": weewx.US, "dateTime": None})
    assert empty["time"] == 0
    assert empty["temp_c"] is None
    assert empty["humidity"] is None


def test_cardinal():
    assert mw.cardinal(None) is None
    assert mw.cardinal(0) == "N"
    assert mw.cardinal(90) == "E"
    assert mw.cardinal(180) == "S"
    assert mw.cardinal(359) == "N"  # wraparound


def test_format_summary_variants():
    s = mw.format_summary(norm())
    assert "20.0°C" in s
    assert "pluie1h 1.2mm" in s and "pluie24h 8.4mm" in s
    # vent sans direction -> "vent Xkm/h" sans cardinal
    wind_only = {k: None for k in norm()}
    wind_only["wind_ms"] = 4.4704
    assert mw.format_summary(wind_only) == "vent 16km/h"
    # aucun champ -> "pas de données"
    none = {k: None for k in norm()}
    assert mw.format_summary(none) == "pas de données"
    # préfixe station + troncature
    assert len(mw.format_summary(norm(), "S" * 300)) <= mw.MAX_TEXT


def test_command():
    assert mw.command("") == "help"
    assert mw.command("  Météo ") == "now"
    assert mw.command("meteo") == "now"
    assert mw.command("now") == "now"
    assert mw.command("vent fort") == "wind"
    assert mw.command("pluie") == "rain"
    assert mw.command("température") == "temp"
    assert mw.command("blabla") == "help"


def test_reply_all_branches():
    assert mw.reply("now", None) == "Aucune donnée météo pour l'instant."
    assert "20.0°C" in mw.reply("now", norm())
    assert "raf." in mw.reply("wind", norm())
    assert mw.reply("wind", norm(wind_ms=None)) == "Vent : —"
    # vent sans rafale ni direction -> "?" et pas de rafale
    r = mw.reply("wind", norm(gust_ms=None, wind_dir=None))
    assert "?" in r and "raf." not in r
    assert mw.reply("rain", norm()) == "Pluie : 1h 1.2 mm · 24h 8.4 mm"
    assert mw.reply("rain", norm(rain_1h=None, rain_24h=None)) == "Pluie : 1h — · 24h —"
    assert "%HR" in mw.reply("temp", norm())
    assert mw.reply("temp", norm(temp_c=None, humidity=None)) == "Température : —"
    assert mw.reply("zzz", norm()) == mw._help_text()


def test_decode_psk():
    assert len(mw.decode_psk("random")) == 32
    assert len(mw.decode_psk(None)) == 32
    raw = base64.b64encode(b"x" * 16).decode()
    assert mw.decode_psk(raw) == b"x" * 16


# --------------------------------------------------------------------------- #
# Sinks
# --------------------------------------------------------------------------- #
def test_fakesink_log_and_file(tmp_path):
    fs = mw.FakeSink()  # branche log.info
    fs.send_text("hi", 2)
    fs.send_dm("yo", 7)
    fs.close()
    assert [c["kind"] for c in fs.calls] == ["text", "dm", "close"]
    path = tmp_path / "dry.log"
    fs2 = mw.FakeSink(str(path))  # branche fichier
    fs2.send_env(norm())
    assert path.read_text().strip().startswith('{"kind": "env"')


def test_tcpsink_send_env_full_and_empty():
    iface = fake_iface()
    sink = mw.TcpSink("h", interface_factory=lambda host: iface)
    assert sink.my_num == 42
    sink.send_env(norm())  # tous les champs présents
    iface.sendData.assert_called_once()
    empty = {k: None for k in norm()}
    empty["time"] = 0
    sink.send_env(empty)  # tous absents -> aucune branche env
    assert iface.sendData.call_count == 2


def test_tcpsink_text_dm_close():
    iface = fake_iface()
    sink = mw.TcpSink("h", interface_factory=lambda host: iface)
    sink.send_text("txt", 3)
    iface.sendText.assert_called_with("txt", channelIndex=3)
    sink.send_dm("dm", 9)
    iface.sendText.assert_called_with("dm", destinationId=9)
    sink.close()
    iface.close.assert_called_once()


def test_make_sink_branches():
    assert isinstance(mw.make_sink({"dry_run": "true"}), mw.FakeSink)
    assert isinstance(mw.make_sink({"transport": "ble"}), mw.FakeSink)  # phase future
    ok = mw.make_sink({"transport": "tcp", "host": "h"},
                      interface_factory=lambda host: fake_iface())
    assert isinstance(ok, mw.TcpSink)

    def boom(host):
        raise OSError("unreachable")

    assert isinstance(mw.make_sink({"host": "h"}, interface_factory=boom), mw.FakeSink)


def test_subscribe():
    with mock.patch("pubsub.pub.subscribe") as sub:
        cb = object()
        mw._subscribe(cb)
        sub.assert_called_once_with(cb, "meshtastic.receive.text")


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
def make_service(cfg=None, sink=None):
    engine = mock.Mock()
    engine.event_callbacks = {}
    config = {"MeshtasticWeather": cfg or {}}
    return mw.MeshtasticWeather(engine, config, sink=sink)


def test_init():
    # __init__ n'ouvre AUCUNE connexion (sink = None jusqu'à la 1re archive).
    svc = make_service()
    assert svc.sink is None
    assert svc.dm_enabled is False  # défaut opt-out
    assert svc.telemetry_interval == 1  # défaut : chaque archive
    assert svc.text_interval == 0  # défaut : texte désactivé
    svc2 = make_service({"dm_enabled": "true"}, sink=mw.FakeSink())
    assert isinstance(svc2.sink, mw.FakeSink)
    assert svc2.dm_enabled is True


def test_quiet_lib_logs_and_toggle():
    logging.getLogger("meshtastic").setLevel(logging.INFO)
    mw._quiet_lib_logs()
    assert logging.getLogger("meshtastic").level == logging.CRITICAL
    with mock.patch.object(mw, "_quiet_lib_logs") as q:
        make_service()  # défaut : silencieux
        q.assert_called_once()
        make_service({"quiet_lib_logs": "false"})  # désactivé
        q.assert_called_once()  # pas rappelé


def test_due():
    svc = make_service()
    svc._n = 1
    assert svc._due(1) is True
    assert svc._due(2) is False
    assert svc._due(0) is False  # désactivé
    svc._n = 4
    assert svc._due(2) is True


def test_rain_totals_from_db():
    svc = make_service()
    svc.latest = norm(time=100000)
    dbm = mock.Mock()
    dbm.std_unit_system = weewx.METRICWX  # pluie en mm
    dbm.table_name = "archive"
    dbm.getSql.return_value = [3.0]  # SUM(rain)
    svc.engine.db_binder.get_manager.return_value = dbm
    assert svc._rain_totals() == (pytest.approx(3.0), pytest.approx(3.0))
    dbm.getSql.return_value = [None]  # pas de données
    assert svc._rain_totals() == (None, None)
    svc.engine.db_binder.get_manager.side_effect = RuntimeError("no db")  # pas de base
    assert svc._rain_totals() == (None, None)


def test_new_archive_record_dm_off_open_send_close():
    # Télémétrie + texte -> ouvrir / envoyer / FERMER (pas de connexion oisive).
    fresh = mock.Mock()
    svc = make_service({"text_interval": "1", "dm_enabled": "false"})
    with mock.patch.object(mw, "make_sink", return_value=fresh):
        svc.new_archive_record(mock.Mock(record=US_RECORD))
    fresh.send_env.assert_called_once()
    fresh.send_text.assert_called_once()
    fresh.close.assert_called_once()
    assert svc.sink is None
    assert svc.latest["temp_c"] == pytest.approx(20.0)


def test_new_archive_record_dm_on_keeps_open_and_subscribes():
    fresh = mock.Mock(spec=["send_env", "send_text", "send_dm", "close", "my_num"])
    svc = make_service({"dm_enabled": "true"})
    with mock.patch.object(mw, "make_sink", return_value=fresh), \
         mock.patch.object(mw, "_subscribe") as sub:
        svc.new_archive_record(mock.Mock(record=US_RECORD))
    fresh.send_env.assert_called_once()
    fresh.close.assert_not_called()  # DM -> connexion gardée ouverte
    assert svc.sink is fresh
    sub.assert_called_once()


def test_new_archive_record_text_interval():
    # télémétrie off, texte toutes les 2 archives : rien à la 1re, texte à la 2e.
    fresh = mock.Mock()
    svc = make_service({"telemetry_interval": "0", "text_interval": "2", "dm_enabled": "false"})
    with mock.patch.object(mw, "make_sink", return_value=fresh) as ms:
        svc.new_archive_record(mock.Mock(record=US_RECORD))  # n=1 -> rien
        ms.assert_not_called()
        svc.new_archive_record(mock.Mock(record=US_RECORD))  # n=2 -> texte
    fresh.send_text.assert_called_once()
    fresh.send_env.assert_not_called()


def test_new_archive_record_nothing_to_send():
    svc = make_service({"telemetry_interval": "0", "text_interval": "0", "dm_enabled": "false"})
    with mock.patch.object(mw, "make_sink") as ms:
        svc.new_archive_record(mock.Mock(record=US_RECORD))
        ms.assert_not_called()  # aucun paquet -> pas de connexion


def test_new_archive_record_dm_on_nothing_due_still_listens():
    # DM actif mais rien à émettre : on rouvre pour écouter, sans envoyer.
    fresh = mock.Mock(spec=["send_env", "send_text", "send_dm", "close", "my_num"])
    svc = make_service({"telemetry_interval": "0", "text_interval": "0", "dm_enabled": "true"})
    with mock.patch.object(mw, "make_sink", return_value=fresh), \
         mock.patch.object(mw, "_subscribe") as sub:
        svc.new_archive_record(mock.Mock(record=US_RECORD))
    fresh.send_env.assert_not_called()
    fresh.send_text.assert_not_called()
    fresh.close.assert_not_called()
    sub.assert_called_once()


def test_new_archive_record_send_error_is_logged():
    fresh = mock.Mock()
    fresh.send_env.side_effect = OSError("boom")
    svc = make_service({"dm_enabled": "false"})
    with mock.patch.object(mw, "make_sink", return_value=fresh):
        svc.new_archive_record(mock.Mock(record=US_RECORD))  # loggé, pas de crash


def test_reconnect_dm_on_real_resubscribes():
    old = mock.Mock()
    old.close.side_effect = RuntimeError("nope")  # avalé
    fresh = mock.Mock(spec=["send_env", "send_text", "send_dm", "close", "my_num"])
    svc = make_service({"dm_enabled": "true"}, sink=old)
    with mock.patch.object(mw, "make_sink", return_value=fresh), \
         mock.patch.object(mw, "_subscribe") as sub:
        svc._reconnect()
    assert svc.sink is fresh
    sub.assert_called_once()  # DM + sink réel -> souscription


def test_reconnect_dm_on_fakesink_skips_subscribe():
    svc = make_service({"dm_enabled": "true"})  # sink None au départ
    with mock.patch.object(mw, "make_sink", return_value=mw.FakeSink()), \
         mock.patch.object(mw, "_subscribe") as sub:
        svc._reconnect()
        sub.assert_not_called()  # DM mais FakeSink -> pas de souscription
    assert isinstance(svc.sink, mw.FakeSink)


def test_on_receive_branches():
    sink = mock.Mock()
    sink.my_num = 42
    svc = make_service(sink=sink)
    svc.latest = norm()
    txt = {"portnum": "TEXT_MESSAGE_APP", "text": "vent"}
    # pas pour nous
    svc._on_receive({"to": 99, "decoded": txt}, None)
    sink.send_dm.assert_not_called()
    # mauvais portnum
    svc._on_receive({"to": 42, "decoded": {"portnum": "POSITION_APP"}}, None)
    sink.send_dm.assert_not_called()
    # happy path
    svc._on_receive({"to": 42, "from": 7, "decoded": txt}, None)
    sink.send_dm.assert_called_once()
    # exception interne (packet non-dict) -> loguée, pas de crash
    svc._on_receive(None, None)


def test_shutdown():
    sink = mock.Mock()
    make_service(sink=sink).shutDown()
    sink.close.assert_called_once()
    sink2 = mock.Mock()
    sink2.close.side_effect = RuntimeError("x")
    make_service(sink=sink2).shutDown()  # avalé


# --------------------------------------------------------------------------- #
# setup-channel + URL/QR + CLI
# --------------------------------------------------------------------------- #
def _lora():
    from meshtastic.protobuf import config_pb2

    return config_pb2.Config.LoRaConfig()


def _real_channel(index):
    # ch.settings/lora doivent être de vrais protobufs (append/CopyFrom).
    from meshtastic.protobuf import channel_pb2

    ch = mock.Mock()
    ch.index = index
    ch.settings = channel_pb2.ChannelSettings()
    return ch


def _node_iface(ch, persisted_name="meteo"):
    # `persisted_name` = ce que la RELECTURE (channels[index]) verra, pour simuler
    # (ou non) la persistance après écriture.
    iface = mock.Mock()
    node = mock.Mock()
    node.getDisabledChannel.return_value = ch
    node.localConfig.lora = _lora()
    if ch is not None:
        verified = mock.Mock()
        verified.settings.name = persisted_name
        node.channels = {ch.index: verified}
    iface.getNode.return_value = node
    return iface, node


def test_channel_url():
    from meshtastic.protobuf import channel_pb2

    s = channel_pb2.ChannelSettings()
    s.name = "meteo"
    url = mw.channel_url(s, _lora())
    assert url.startswith("https://meshtastic.org/e/#")
    assert "=" not in url.split("#", 1)[1]  # padding retiré


def test_safe_close_swallows():
    iface = mock.Mock()
    iface.close.side_effect = RuntimeError("x")
    mw._safe_close(iface)  # ne lève pas


def test_setup_channel_creates_secondary_confirmed():
    iface, node = _node_iface(_real_channel(3), persisted_name="meteo")
    idx, psk_b64, url = mw.setup_channel(
        "h", "meteo", "random", interface_factory=lambda host: iface, delay=0
    )
    assert idx == 3
    assert len(base64.b64decode(psk_b64)) == 32  # PSK 256 bits générée
    assert url.startswith("https://meshtastic.org/e/#")
    node.writeChannel.assert_called_once_with(3)  # confirmé du 1er coup


def test_setup_channel_provided_psk():
    raw = base64.b64encode(b"z" * 16).decode()
    iface, _ = _node_iface(_real_channel(1), persisted_name="n")
    idx, b64, _url = mw.setup_channel(
        "h", "n", raw, interface_factory=lambda host: iface, delay=0
    )
    assert idx == 1
    assert base64.b64decode(b64) == b"z" * 16


def test_setup_channel_no_free_slot():
    iface, _ = _node_iface(None)
    with pytest.raises(RuntimeError, match="aucun canal libre"):
        mw.setup_channel("h", "n", interface_factory=lambda host: iface, delay=0)


def test_setup_channel_retries_until_confirmed_then_gives_up():
    # La relecture ne voit jamais le nom -> non persisté -> réessais puis échec.
    iface, node = _node_iface(_real_channel(3), persisted_name="")
    with pytest.raises(RuntimeError, match="non confirmée"):
        mw.setup_channel(
            "h", "meteo", interface_factory=lambda host: iface, attempts=2, delay=0
        )
    assert node.writeChannel.call_count == 2  # a bien réessayé


def test_print_qr_with_and_without_lib():
    import io

    out = io.StringIO()
    mw.print_qr("https://meshtastic.org/e/#abc", out=out)  # qrcode installé -> QR ASCII
    assert out.getvalue().strip()
    out2 = io.StringIO()
    with mock.patch.dict("sys.modules", {"qrcode": None}):  # import qrcode -> ImportError
        mw.print_qr("https://x", out=out2)
    assert "pip install qrcode" in out2.getvalue()


def test_main_setup_channel(capsys):
    ret = (2, "QUJD", "https://meshtastic.org/e/#abc")
    with mock.patch.object(mw, "setup_channel", return_value=ret):
        rc = mw.main(["setup-channel", "--host", "node.local", "--name", "meteo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "index=2" in out and "meshtastic.org/e/#abc" in out


def test_main_no_command():
    assert mw.main([]) == 1
