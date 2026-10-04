# EGD Console in a container — see docker-compose.yml.
FROM python:3.12-alpine

# git reads branches and the trail; mounted projects belong to the host user, so let it read them
RUN apk add --no-cache git \
 && git config --system --add safe.directory '*'

# EGD needs nothing beyond the standard library: run it straight from the source, no pip step
COPY src /opt/egd/src
COPY bin/egd /opt/egd/bin/egd
COPY LICENSE /opt/egd/
RUN ln -s /opt/egd/bin/egd /usr/local/bin/egd

# compose runs it as your own uid (no root-owned files in your repositories): any uid may write
# its home and /data, the console's settings
ENV HOME=/home/egd
RUN mkdir -p /home/egd/.config /data && chmod -R 777 /home/egd /data

WORKDIR /host
EXPOSE 8780
# Listening on every interface, so a token is required: one is generated and printed in the logs
# (`docker logs <container>`), or set EGD_TOKEN. The Host check alone is not access control.
# docker-compose.yml publishes on 127.0.0.1 only and turns the token off there. --lan-write: clients
# reach the console from Docker's bridge address, never loopback, so actions need it.
CMD ["egd", "console", "serve", "--host", "0.0.0.0", "--port", "8780", "--lan-write"]
