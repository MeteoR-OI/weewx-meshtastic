# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MeteoR-OI
"""Installateur d'extension WeeWX (weectl extension install / wee_extension)."""
from weecfg.extension import ExtensionInstaller


def loader():
    return MeshtasticWeatherInstaller()


class MeshtasticWeatherInstaller(ExtensionInstaller):
    def __init__(self):
        super().__init__(
            version="0.1.0",
            name="meshtastic_weather",
            description="Pousse la météo WeeWX vers un node Meshtastic "
            "(télémétrie EnvironmentMetrics + résumé sur un canal + bot DM).",
            author="MeteoR-OI",
            author_email="contact@meteor-oi.re",
            archive_services="user.meshtastic_weather.MeshtasticWeather",
            config={
                "MeshtasticWeather": {
                    "transport": "tcp",
                    "host": "192.168.1.20",
                    "channel_index": "2",
                    "station_name": "Ma station",
                    "send_telemetry": "true",
                    "send_text": "true",
                    "dm_enabled": "true",
                    "dry_run": "false",
                }
            },
            files=[("bin/user", ["bin/user/meshtastic_weather.py"])],
        )
