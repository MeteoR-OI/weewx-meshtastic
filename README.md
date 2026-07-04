# weewx-meshtastic

[![CI](https://github.com/MeteoR-OI/weewx-meshtastic/actions/workflows/ci.yml/badge.svg)](https://github.com/MeteoR-OI/weewx-meshtastic/actions/workflows/ci.yml)
![coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)

Extension **WeeWX** qui pousse la météo d'une station (ex. Davis Vantage Pro 2)
vers un **node Meshtastic** à chaque enregistrement **ARCHIVE**. Elle :

1. fait émettre au node une **télémétrie environnementale** (`EnvironmentMetrics` :
   température, humidité, pression, vent) — visible dans l'app Meshtastic, sur les
   autres nodes, et exploitable par [meshforge](https://github.com/Robin-Lune/MeshForge) ;
2. diffuse un **résumé texte** sur un **canal dédié** ;
3. répond à des **interrogations en message direct (DM)** : `météo`, `vent`, `pluie`, `temp`, `aide`.

Une seule `StdService` détient **une** connexion persistante au node (un seul
client). Cible : **WeeWX 4/5** (Python 3).

## Installation

```bash
# WeeWX 5
weectl extension install https://github.com/MeteoR-OI/weewx-meshtastic/archive/refs/heads/main.zip
# WeeWX 4
wee_extension --install weewx-meshtastic.zip
# Dépendance runtime (dans le venv de WeeWX) :
pip install meshtastic
```

Puis dans `weewx.conf` :

```ini
[MeshtasticWeather]
    transport = tcp            # tcp (défaut) | serial | ble (serial/ble = à venir)
    host = 192.168.1.20        # IP du node (API TCP port 4403) — si transport=tcp
    channel_index = 2          # index du canal dédié (voir « Canal dédié »)
    station_name = "Ma station"
    send_telemetry = true
    send_text = true
    dm_enabled = true
    dry_run = false            # true = logue au lieu d'émettre (tests hors-ligne)
```

Le service est ajouté automatiquement à `[Engine][[Services]] archive_services`.

## Canal dédié

À créer une fois, via TCP, **sans toucher aux canaux existants** (remplit le premier
slot libre ; le node redémarre ensuite) :

```bash
python -m meshtastic_weather setup-channel --host 192.168.1.20 --name meteo
# → affiche l'index (à mettre dans channel_index) et la PSK (à partager avec meshforge)
```

## Commandes DM

| Message | Réponse |
|---------|---------|
| `météo` / `now` | résumé complet |
| `vent` | vitesse + rafale + direction |
| `pluie` | taux + cumul du jour |
| `temp` | température + humidité |
| `aide` | liste des commandes |

## Transports

`tcp` (WiFi) est supporté aujourd'hui. `serial` (USB) et **`ble`** (Bluetooth, pour
les nodes BT-only comme le Heltec T114, via le BT d'un Raspberry Pi ou un dongle)
sont prévus : ils partagent l'interface `MeshtasticSink`, il suffira d'ajouter la
classe correspondante — aucune refonte.

## Développement & tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest                       # tests unitaires + couverture 100% (statements + branches)
ruff check .                 # lint
# Intégration contre un node Meshtastic SIMULÉ (aucun matériel) :
docker compose -f test/docker-compose.test.yml up --build \
    --abort-on-container-exit --exit-code-from tester
```

La **CI** rejoue : lint, tests unitaires (matrice Python 3.9–3.12, WeeWX 5) avec
**gate de couverture 100 %**, et l'intégration Docker contre `meshtasticd -s`.

> WeeWX n'est publié sur PyPI qu'à partir de la 5.0 ; les tests tournent donc
> contre WeeWX 5. La compat **WeeWX 4** repose sur les API stables utilisées
> (`StdService`, `ExtensionInstaller`, `weewx.units`), inchangées entre 4 et 5.

## Licence

GPL-3.0-or-later — voir [LICENSE](LICENSE).
