ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apk add --no-cache tzdata

WORKDIR /opt/app

COPY app/requirements.txt /opt/app/requirements.txt
RUN pip install --no-cache-dir -r /opt/app/requirements.txt

COPY app /opt/app/app
COPY rootfs /

RUN chmod a+x /etc/services.d/sports-live/run /etc/services.d/sports-live/finish

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD wget -qO- http://127.0.0.1:8099/api/healthz >/dev/null 2>&1 || exit 1

LABEL io.hass.version="0.1.1" \
      io.hass.type="addon" \
      io.hass.arch="aarch64|amd64|armv7"
