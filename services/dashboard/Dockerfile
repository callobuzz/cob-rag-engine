# The dashboard (dashboard D12): Node 22 builds the static app, nginx serves it.
#
#   docker build -t cob-rag-dashboard:local services/dashboard
#   docker run -p 127.0.0.1:8090:8080 -e RAG_DASHBOARD_ENGINE_URL=http://127.0.0.1:8000 cob-rag-dashboard:local
#
# At start, /docker-entrypoint.d/40-cob-config.sh writes config.js and the nginx site (with a
# CSP limited to the engine and scraper) from RAG_DASHBOARD_ENGINE_URL, RAG_DASHBOARD_SCRAPER_URL
# and RAG_DASHBOARD_USD_TO_INR. nginx runs as the image's non-root `nginx` user on port 8080.

# --- Build -------------------------------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# --- Serve -------------------------------------------------------------------------------------
FROM nginx:1.27-alpine

# Non-root: no `user` directive, the pid file in /tmp, and write access only where the start
# script and nginx write (the generated config.js and site, nginx's temp files).
RUN sed -i -e '/^user /d' -e 's#^pid .*#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf \
	&& rm -f /etc/nginx/conf.d/default.conf \
	&& mkdir -p /etc/nginx/cob \
	&& chown -R nginx:nginx /var/cache/nginx /etc/nginx/conf.d

COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf.template deploy/headers.conf /etc/nginx/cob/
COPY deploy/40-cob-config.sh /docker-entrypoint.d/40-cob-config.sh

# config.js is generated at start; the build's development copy is replaced by an empty file the
# nginx user may overwrite. CRLFs are stripped in case of a Windows checkout without .gitattributes.
RUN sed -i 's/\r$//' /docker-entrypoint.d/40-cob-config.sh \
	&& chmod 755 /docker-entrypoint.d/40-cob-config.sh \
	&& rm -f /usr/share/nginx/html/config.js \
	&& touch /usr/share/nginx/html/config.js \
	&& chown nginx:nginx /usr/share/nginx/html/config.js

ENV RAG_DASHBOARD_ENGINE_URL=http://127.0.0.1:8000 \
	RAG_DASHBOARD_SCRAPER_URL=http://127.0.0.1:8001 \
	RAG_DASHBOARD_USD_TO_INR=85

USER nginx
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
