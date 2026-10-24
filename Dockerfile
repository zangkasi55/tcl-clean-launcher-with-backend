FROM python:3.12-slim AS builder
WORKDIR /build
COPY controller/requirements.lock controller/requirements.lock
RUN pip install --no-cache-dir --prefix=/install -r controller/requirements.lock
COPY controller controller
RUN pip install --no-cache-dir --no-deps --prefix=/install ./controller

FROM python:3.12-slim
RUN useradd --uid 10001 --create-home tvcontrol \
    && mkdir -p /data /app/licenses \
    && chown 10001:10001 /data
COPY --from=builder /install /usr/local
COPY dist/tvhome-release.apk /app/launcher.apk
COPY LICENSE launcher/FONT-LICENSE-OFL.txt /app/licenses/
ENV TVCONTROL_DATA_DIR=/data \
    TVCONTROL_APK=/app/launcher.apk \
    TVCONTROL_LISTEN=0.0.0.0 \
    TVCONTROL_PORT=8787 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /app
EXPOSE 8787
CMD ["python", "-m", "tvcontrol"]
