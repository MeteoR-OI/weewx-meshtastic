# Test LIVE : WeeWX + driver Simulator + notre extension, poussant vers un VRAI
# node Meshtastic (par défaut 192.168.1.20). Génère de fausses données météo et
# les envoie toutes les 60 s. À NE PAS confondre avec l'intégration CI (node
# simulé) : ici on vise du matériel réel sur le LAN.
#
#   docker build -t weewx-meshtastic-live -f test/live/Dockerfile .
#   docker run --rm --name wm-live weewx-meshtastic-live         # défaut 192.168.1.20
#   docker run --rm -e NODE_HOST=192.168.1.30 weewx-meshtastic-live
FROM python:3.12-slim

RUN pip install --no-cache-dir weewx meshtastic

# Station WeeWX minimale avec le driver Simulator.
RUN weectl station create /data \
      --driver=weewx.drivers.simulator \
      --location="Docker Sim" --altitude="100,meter" \
      --latitude=-21.0 --longitude=55.5 --units=metricwx \
      --register=n --no-prompt

# Notre extension, exposée comme `user.meshtastic_weather` pour WeeWX.
COPY bin/user/meshtastic_weather.py /data/bin/user/meshtastic_weather.py

# Archive 60 s, records logiciels, on branche le service, on coupe restful/report.
RUN sed -i \
      -e 's/archive_interval = 300/archive_interval = 60/' \
      -e 's/record_generation = hardware/record_generation = software/' \
      -e 's#archive_services = weewx.engine.StdArchive#archive_services = weewx.engine.StdArchive, user.meshtastic_weather.MeshtasticWeather#' \
      -e 's/^\(\s*restful_services =\).*/\1 ,/' \
      -e 's/^\(\s*report_services =\).*/\1 ,/' \
      /data/weewx.conf

COPY test/live/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV NODE_HOST=192.168.1.20
ENV CHANNEL_INDEX=0
ENTRYPOINT ["/entrypoint.sh"]
