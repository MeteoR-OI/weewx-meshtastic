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
    connect_warmup = 3         # s d'attente après connexion avant d'émettre (voir « Robustesse »)
    channel_index = 2          # index du canal dédié (voir « Canal dédié »)
    station_name = "Ma station"
    send_telemetry = true
    send_text = true
    dm_enabled = false         # true = bot DM (garde la connexion ouverte, cf. « Robustesse »)
    dry_run = false            # true = logue au lieu d'émettre (tests hors-ligne)
```

Le service est ajouté automatiquement à `[Engine][[Services]] archive_services`.

## Canal dédié

À créer une fois, via TCP, **sans toucher aux canaux existants** (remplit le premier
slot libre ; le node redémarre ensuite) :

```bash
python -m meshtastic_weather setup-channel --host 192.168.1.20 --name meteo
```

Affiche l'**index** (à reporter dans `channel_index`), la **PSK** (à partager avec meshforge),
et un **lien + QR code** partageable du canal (`https://meshtastic.org/e/#…`) pour l'ajouter
d'un scan sur d'autres appareils :

```
Canal 'meteo' créé : index=3
PSK (base64) : t1BkNc7f…
Lien/QR      : https://meshtastic.org/e/#CikSIL…
█▀▀▀▀▀▀▀██▀▀█▀▀▀███▀████…   ← QR ASCII (nécessite `pip install qrcode`)
→ mettre channel_index = 3 dans [MeshtasticWeather].
```

## Commandes DM

| Message | Réponse |
|---------|---------|
| `météo` / `now` | résumé complet |
| `vent` | vitesse + rafale + direction |
| `pluie` | taux + cumul du jour |
| `temp` | température + humidité |
| `aide` | liste des commandes |

> **Robustesse / 1er paquet** : le node **perd le premier paquet émis juste après connexion**.
> Comme on ouvre une connexion fraîche à chaque archive et que la **télémétrie** est le 1er
> envoi, on attend `connect_warmup` secondes (défaut 3) après connexion avant d'émettre —
> sinon la télémétrie ne partirait pas. Augmentez cette valeur si un node est plus lent à démarrer.
>
> **Robustesse / DM** : le node ferme les connexions TCP **inactives** (quelques dizaines de
> secondes), ce que la lib meshtastic logue en `ERROR … reader … timed out`. Par défaut
> (`dm_enabled = false`) l'extension **ouvre / envoie / ferme** à chaque archive : aucune
> connexion oisive, donc **pas de bruit**, et une pousse fiable toutes les 5 min. Activer le
> bot **DM** (`dm_enabled = true`) **maintient la connexion ouverte** pour écouter — au prix de
> ce message de lecteur périodique, et l'écoute n'est fiable que peu après chaque archive. Pour
> un DM permanent et propre, un daemon dédié (connexion maintenue avec keepalive) est à ajouter
> (phase future).

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

### Test « live » : WeeWX Simulator → vrai node

Fait tourner WeeWX (driver Simulator, archive 60 s) + l'extension dans un conteneur, en
poussant vers un **vrai** node Meshtastic sur le LAN (par défaut `192.168.1.20`) :

```bash
docker build -t weewx-meshtastic-live -f test/live/Dockerfile .
docker run --rm -e NODE_HOST=192.168.1.20 -e CHANNEL_INDEX=3 weewx-meshtastic-live
# logs attendus : « prêt (sink=TcpSink) » puis « archive envoyée (TcpSink, canal 3) »
```

La **CI** rejoue : lint, tests unitaires (matrice Python 3.9–3.12, WeeWX 5) avec
**gate de couverture 100 %**, et l'intégration Docker contre `meshtasticd -s`.

> WeeWX n'est publié sur PyPI qu'à partir de la 5.0 ; les tests tournent donc
> contre WeeWX 5. La compat **WeeWX 4** repose sur les API stables utilisées
> (`StdService`, `ExtensionInstaller`, `weewx.units`), inchangées entre 4 et 5.

## Licence

GPL-3.0-or-later — voir [LICENSE](LICENSE).
