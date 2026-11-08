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
# Note: this creates /root/.local/bin
RUN pipx install uv
RUN npm install -g corepack
RUN npm install -g vite
ENV PATH="/root/.local/bin:${PATH}"

# Don't pollute directory structure with __pycache__
ENV PYTHONPYCACHEPREFIX='/tmp'

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
ENV UV_LINK_MODE=copy

WORKDIR /run/secretary-records

EXPOSE 4500
