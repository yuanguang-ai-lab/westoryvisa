FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:101 \
    HOME=/app/runtime/home \
    XDG_CONFIG_HOME=/app/runtime/config \
    XDG_CACHE_HOME=/app/runtime/cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        dbus-x11 \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        novnc \
        openbox \
        websockify \
        x11-utils \
        x11vnc \
        xdotool \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY standalone-agent /app/standalone-agent
COPY standalone-agent-v2 /app/standalone-agent-v2

RUN pip install --no-cache-dir \
        "/app/standalone-agent[secure]" \
        /app/standalone-agent-v2 \
        "playwright>=1.55,<2" \
    && python -m playwright install --with-deps chromium \
    && addgroup --system docflow \
    && adduser --system --ingroup docflow --home /app docflow \
    && mkdir -p \
        /app/data \
        /app/runtime/home \
        /app/runtime/config \
        /app/runtime/cache \
    && chown -R docflow:docflow /app/data /app/runtime /ms-playwright

COPY deploy/agent-v2-entrypoint.sh /usr/local/bin/docflow-agent-v2-entrypoint
RUN chmod 0755 /usr/local/bin/docflow-agent-v2-entrypoint

USER docflow

EXPOSE 8766 6080 6081

ENTRYPOINT ["/usr/local/bin/docflow-agent-v2-entrypoint"]
