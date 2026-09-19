FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:102 \
    HOME=/app/runtime/home \
    XDG_CONFIG_HOME=/app/runtime/config \
    XDG_CACHE_HOME=/app/runtime/cache

# Chrome is installed only from Google's signed Debian repository.  A failed
# install/version check fails this image build; it never falls back to bundled
# Chromium while claiming to be the pilot runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        dbus-x11 \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        gnupg \
        novnc \
        openbox \
        websockify \
        wget \
        x11-utils \
        x11vnc \
        xdotool \
        xvfb \
    && install -m 0755 -d /etc/apt/keyrings \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
    && chmod 0644 /etc/apt/keyrings/google-chrome.gpg \
    && printf '%s\n' \
       'deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main' \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && google-chrome-stable --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY standalone-agent /app/standalone-agent
COPY standalone-agent-v2 /app/standalone-agent-v2
COPY scripts/human_first_chrome_smoke.py /app/scripts/human_first_chrome_smoke.py

RUN pip install --no-cache-dir \
        "/app/standalone-agent[secure]" \
        /app/standalone-agent-v2 \
        "playwright>=1.55,<2" \
    && addgroup --system docflow \
    && adduser --system --ingroup docflow --home /app docflow \
    && mkdir -p \
        /app/data \
        /app/runtime/home \
        /app/runtime/config \
        /app/runtime/cache \
    && chown -R docflow:docflow /app/data /app/runtime

COPY deploy/agent-v2-entrypoint.sh /usr/local/bin/docflow-agent-v2-entrypoint
COPY deploy/chrome-pilot-desktop.sh /usr/local/bin/docflow-chrome-pilot-desktop
RUN chmod 0755 \
        /usr/local/bin/docflow-agent-v2-entrypoint \
        /usr/local/bin/docflow-chrome-pilot-desktop

USER docflow

EXPOSE 8766 6080 6081

ENTRYPOINT ["/usr/local/bin/docflow-agent-v2-entrypoint"]
