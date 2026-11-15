FROM alpine:3.22.2
RUN echo @testing https://dl-cdn.alpinelinux.org/alpine/edge/testing >> /etc/apk/repositories
RUN apk upgrade -Ua --clean-protected
RUN apk add \
    caddy \
    cyrus-sasl-dev \
    gcc \
    git \
    jq \
    linux-headers \
    make \
    mkcert@testing \
    musl-dev \
    npm \
    openldap-dev \
    pipx \
    procps-ng \
    python3 \
    python3-dev \
    subversion \
    tzdata \
    --no-cache
RUN npm install -g corepack
RUN npm install -g vite
# The uv that .tool-versions names, which is the one CI uses. The copy comes last, so a change to
# .tool-versions rebuilds this layer and nothing before it. This creates /root/.local/bin.
COPY .tool-versions /tmp/.tool-versions
RUN version="$(sed -En 's/^uv +([^ ]+).*/\1/p' /tmp/.tool-versions)" \
    && pipx install "uv==${version}" \
    && rm /tmp/.tool-versions
ENV PATH="/root/.local/bin:${PATH}"

# The Python that .python-version names, which is the one production and CI run. uv prefers it to
# the python3 of the image, which stays for pipx. A change to .python-version rebuilds this layer.
COPY .python-version /tmp/.python-version
RUN uv python install "$(cat /tmp/.python-version)" \
    && rm /tmp/.python-version

# Don't pollute directory structure with __pycache__
ENV PYTHONPYCACHEPREFIX='/tmp'

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
ENV UV_LINK_MODE=copy

WORKDIR /run/secretary-records

EXPOSE 4500
