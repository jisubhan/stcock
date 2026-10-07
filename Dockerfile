FROM node:24-bookworm-slim AS web-builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 \
    API_ORIGIN=http://127.0.0.1:8000
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
RUN npm ci --no-audit --no-fund
COPY apps/web apps/web
RUN npm run build

FROM python:3.12-slim-bookworm AS api-builder
WORKDIR /app
COPY apps/api/requirements.lock.txt /tmp/requirements.lock.txt
RUN python -m venv /app/.venv \
    && /app/.venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock.txt

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 radar \
    && mkdir -p /app/.runtime \
    && chown -R radar:radar /app
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TOSS_DATA_MODE=demo \
    API_DATA_DIR=/app/.runtime \
    PORT=10000
COPY --from=web-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=api-builder --chown=radar:radar /app/.venv /app/.venv
COPY --from=web-builder --chown=radar:radar /app/apps/web/.next/standalone /app
COPY --from=web-builder --chown=radar:radar /app/apps/web/.next/static /app/apps/web/.next/static
COPY --from=web-builder --chown=radar:radar /app/apps/web/public /app/apps/web/public
COPY --chown=radar:radar apps/api/app apps/api/app
COPY --chown=radar:radar config config
COPY --chown=radar:radar scripts/start-prod.mjs scripts/start-prod.mjs
USER radar
EXPOSE 10000
CMD ["node", "scripts/start-prod.mjs"]
