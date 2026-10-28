# App image for docker-compose.yml (clean-VPS reproducibility). Dependencies are installed
# from the hash-pinned deploy/requirements.lock, never resolved at build time.
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin muse \
 && install -d -o muse -g muse -m 0700 /data

WORKDIR /app
COPY deploy/requirements.lock deploy/requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps -r deploy/requirements.lock
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STUDIO_DATA_DIR=/data
USER muse
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "studio.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-proxy-headers", "--no-server-header", "--limit-concurrency", "64"]
