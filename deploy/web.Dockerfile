# Web: builds the static front-end (it reads ../examples at build time) and serves it with
# Caddy, which also terminates HTTPS and proxies /api/* to the API container.
# Build context: repository root.
FROM node:22-slim AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci
COPY examples examples
COPY frontend frontend
RUN cd frontend && npm run build

FROM caddy:2
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/frontend/out /srv
