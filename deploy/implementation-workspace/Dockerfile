FROM node:24.15.0-bookworm-slim AS node
FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update && apt-get install -y --no-install-recommends \
    git tmux curl ca-certificates build-essential procps unzip openssh-client iptables util-linux \
 && rm -rf /var/lib/apt/lists/*
ARG CODE_SERVER_VERSION=4.139.1
ARG CODE_SERVER_SHA256=53029be6c5781b7bca49b815fcc9a2a3fc111813ad8c9965b2c0f0d2985a0674
RUN curl -fLsS --retry 3 -o /tmp/code-server.tar.gz \
    https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-amd64.tar.gz \
 && echo "${CODE_SERVER_SHA256}  /tmp/code-server.tar.gz" | sha256sum -c - \
 && mkdir /opt/code-server && tar -xzf /tmp/code-server.tar.gz -C /opt/code-server --strip-components=1 \
 && rm /tmp/code-server.tar.gz \
 && npm install -g @earendil-works/pi-coding-agent@0.87.1 \
 && pip install --no-cache-dir numpy==2.5.3 scipy==1.18.1 meent==0.13.2 pytest
RUN groupadd -g 1000 developer && useradd -u 1000 -g 1000 -m -s /bin/bash developer
COPY start.sh /opt/workspace/start.sh
COPY network.sh /opt/workspace/network.sh
ENV HOME=/home/developer PATH=/home/developer/.local/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONUNBUFFERED=1
WORKDIR /work
EXPOSE 8080
ENTRYPOINT ["bash", "/opt/workspace/network.sh"]
