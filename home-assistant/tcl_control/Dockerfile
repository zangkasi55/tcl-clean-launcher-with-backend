FROM ghcr.io/home-assistant/base:3.24
RUN apk add --no-cache python3 py3-aiohttp
COPY proxy.py /app/proxy.py
WORKDIR /app
CMD ["python3", "/app/proxy.py"]
