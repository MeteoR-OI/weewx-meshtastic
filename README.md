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

Implémentée en une `StdService` WeeWX. Cible : **WeeWX 4/5** (Python 3).

## Architecture

```
        Davis Vantage Pro 2  (station météo)
                │  câble/USB — trames LOOP puis ARCHIVE (ex. toutes les 5 min)
                ▼
   ┌──────────────────────────────────────────────────┐
   │ WeeWX   (PC / Raspberry Pi)                      │
   │   • pilote la station Davis                      │
   │   • extension weewx-meshtastic (StdService)      │  ← ce dépôt
   │   • bot DM : parse la commande reçue et          │
   │     compose la réponse  ← TOUT est traité ICI    │
   └────────────┬─────────────────────────────────────┘
                │  API Meshtastic — TCP/WiFi (défaut) OU BLE (BT-only)
                │  ↓ à chaque ARCHIVE : télémétrie EnvironmentMetrics
                │       + [option] résumé texte  "PAM290 — 22°C · …"
                │  ↑ DM reçu    ↓ réponse    (le node relaie, il ne calcule rien)
                ▼
   ┌──────────────────────────────────────────────────┐
   │ Node Meshtastic « station météo » du maillage    │
   │  (n'importe quel modèle : WiFi/TCP ou BLE)       │
   │  RELAIS seulement — ne traite aucune commande    │
   └────────────┬─────────────────────────────────────┘
                │  LoRa (radio maillée — fréquence gérée par le node : 868/915/433…)
      ┌─────────┴───────┬──────────────────┬───────────────────┐
      ▼                 ▼                  ▼                   ▼
      autres            node d'un          node-passerelle     meshforge
      nodes             utilisateur qui    MQTT ── MQTT ──▶    (carte + calque
      (voient la        envoie un DM                            météo — phase 2)
       télémétrie)      « météo ? »
```

- **Télémétrie** (structurée, par station) : le cœur du dispositif — à chaque ARCHIVE par défaut.
- **Texte** sur le canal partagé `meteo` : optionnel (le canal peut réunir plusieurs stations).
- **DM (bot)** : un **autre** node (l'appli Meshtastic d'un utilisateur) envoie `météo ?` en
  message direct. Le node « station météo » **ne traite rien** : il transmet le texte reçu à
  WeeWX (TCP/BLE), et c'est **l'extension** (`_on_receive`) qui parse la commande et compose la
  réponse à partir du dernier ARCHIVE ; la réponse repart WeeWX → node → LoRa → node demandeur
  (best-effort, cf. « Robustesse »).
- **Connexion** : ouverte à l'émission puis refermée (le node coupe les connexions oisives).

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
    transport = tcp            # tcp (défaut, WiFi) | ble (BT-only) | serial (à venir)
    host = meshtastic.local    # hôte/IP du node (API TCP port 4403) — À ADAPTER (si tcp)
    connect_warmup = 3         # s d'attente après connexion avant d'émettre (voir « Robustesse »)
    channel_index = 2          # index du canal dédié (voir « Canal dédié »)
    station_id = STATION1      # identifiant station, préfixe du message (le canal est partagé)
    telemetry_interval = 1     # télémétrie toutes les N archives (1 = chaque ; 0 = jamais)
    text_interval = 0          # message texte toutes les M archives (0 = jamais ; ex. 6)
    dm_enabled = false         # true = bot DM (garde la connexion ouverte, cf. « Robustesse »)
    quiet_lib_logs = true      # tait le bruit ERROR de la lib meshtastic (géré par reconnexion)
    dry_run = false            # true = logue au lieu d'émettre (tests hors-ligne)
```

> Valeurs à **adapter** (`host`, `channel_index`, `station_id`). Le **canal meteo est
> partagé** entre plusieurs stations/modules : garde `text_interval` bas ou nul et laisse la
> **télémétrie** (structurée, par station) faire l'essentiel.

Le service est ajouté automatiquement à `[Engine][[Services]] archive_services`.

## Canal dédié

À créer une fois, via TCP, **sans toucher aux canaux existants** (remplit le premier
slot libre ; le node redémarre ensuite) :

```bash
python -m meshtastic_weather setup-channel --host <hôte-ou-ip-du-node> --name meteo
```

Affiche l'**index** (à reporter dans `channel_index`), la **PSK** (à partager avec meshforge),
et un **lien + QR code** partageable du canal (`https://meshtastic.org/e/#…`) pour l'ajouter
d'un scan sur d'autres appareils :

```
Canal 'meteo' créé : index=3
PSK (base64) : AbCdEf0123…            (exemple)
Lien/QR      : https://meshtastic.org/e/#CgUS…   (exemple)
█▀▀▀▀▀▀▀██▀▀█▀▀▀███▀████…   ← QR ASCII (nécessite `pip install qrcode`)
→ mettre channel_index = 3 dans [MeshtasticWeather].
```

> `setup-channel` passe par **TCP**. Pour un node **BLE-only**, crée le canal via l'app
> Meshtastic (ou importe le lien/QR ci-dessus), puis renseigne son `channel_index`.

## Commandes DM

| Message | Réponse |
|---------|---------|
| `météo` / `now` | résumé complet |
| `vent` | vitesse + rafale + direction |
| `pluie` | cumuls 1 h et 24 h |
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

Réglés par `transport` :

- **`tcp`** (WiFi) — défaut ; `host` = hôte/IP du node.
- **`ble`** (Bluetooth LE) — pour les **nodes BT-only** ; `ble_address` = MAC/nom/UUID BLE.
- `serial` (USB) — à venir.

```ini
[MeshtasticWeather]
    transport = ble
    ble_address = XX:XX:XX:XX:XX:XX   # MAC (Linux) ou nom/UUID (macOS)
```

> **BLE — appairage & Docker** : `ble` requiert `bleak` (installé avec `meshtastic`) et un
> **Bluetooth accessible NATIVEMENT**. Au **1er contact**, le node affiche un **PIN** à saisir
> (appairage/bonding) ; ensuite la connexion est directe. Validé nativement sur macOS (saisir le
> PIN au prompt) et Linux (`bluetoothctl pair …`). En revanche **Docker Desktop sur macOS n'a pas
> accès au BT de l'hôte** → pour `ble`, lance WeeWX **directement** sur la machine BT (Raspberry
> Pi, Mac…), ou en Docker **sur un hôte Linux** avec passthrough BlueZ/D-Bus (`--net=host`,
> `/var/run/dbus`). Le transport `tcp` (WiFi) reste le plus simple si le node a du WiFi.

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
poussant vers un **vrai** node Meshtastic sur le LAN. Renseigne **tes** valeurs via `-e` :

```bash
docker build -t weewx-meshtastic-live -f test/live/Dockerfile .
docker run --rm \
    -e NODE_HOST=<ip-de-ton-node> -e CHANNEL_INDEX=<index-du-canal> \
    -e STATION_ID=<ton-id> -e TEXT_INTERVAL=1 -e DM_ENABLED=true \
    weewx-meshtastic-live
# logs : « prêt (…) » puis « archive #N → télémétrie [+ texte] envoyé (TcpSink, canal N) »
```

La **CI** rejoue : lint, tests unitaires (matrice Python 3.9–3.12, WeeWX 5) avec
**gate de couverture 100 %**, et l'intégration Docker contre `meshtasticd -s`.

> WeeWX n'est publié sur PyPI qu'à partir de la 5.0 ; les tests tournent donc
> contre WeeWX 5. La compat **WeeWX 4** repose sur les API stables utilisées
> (`StdService`, `ExtensionInstaller`, `weewx.units`), inchangées entre 4 et 5.

## Licence

GPL-3.0-or-later — voir [LICENSE](LICENSE).
