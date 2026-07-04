# Déploiement

## Prérequis
- WeeWX **4 ou 5** (Python 3) opérationnel sur la machine qui pilote la station.
- Un node Meshtastic joignable en **WiFi** (API TCP, port 4403). Repérer son IP.
- La lib `meshtastic` installée **dans le même environnement Python que WeeWX** :
  ```bash
  # exemple si WeeWX tourne dans un venv
  /home/weewx/venv/bin/pip install meshtastic
  ```

## Mise en service
1. Installer l'extension (voir le README).
2. Créer le canal dédié : `python -m meshtastic_weather setup-channel --host <IP> --name meteo`.
   Noter l'**index** (→ `channel_index`) et la **PSK** (à réutiliser côté meshforge pour
   déchiffrer la télémétrie si elle passe par ce canal).
3. Renseigner `[MeshtasticWeather]` dans `weewx.conf` (`host`, `channel_index`, `station_name`).
4. Redémarrer WeeWX. À chaque ARCHIVE, le log doit montrer `MeshtasticWeather … prêt (sink=TcpSink)`.

## Vérification
- App Meshtastic → fiche du node : les EnvironmentMetrics apparaissent.
- Canal dédié : le résumé texte arrive à chaque intervalle d'archive.
- DM au node : `météo`, `vent`, `pluie`, `temp`, `aide` → réponses.

## Dépannage
- **`sink=FakeSink` dans les logs** : `dry_run=true`, ou lib `meshtastic` absente, ou node
  injoignable → l'extension bascule en dry-run (logue au lieu d'émettre). Vérifier `host`
  et l'install de `meshtastic`.
- **Rien sur le canal** : vérifier `channel_index` (celui rendu par `setup-channel`).
- **Reconnexions fréquentes** : coupures WiFi / reboots du node ; l'extension se reconnecte
  automatiquement à l'ARCHIVE suivant.

## Notes
- L'**écran OLED** du node affiche ses capteurs I2C locaux ; la télémétrie injectée est
  diffusée sur le mesh mais n'apparaît pas forcément sur l'écran du node lui-même.
- La **pluie** n'existe pas dans `EnvironmentMetrics` : elle n'est présente que dans le
  résumé texte et les réponses DM.
