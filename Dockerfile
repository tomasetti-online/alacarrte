FROM python:3.12-slim

# ffmpeg = audio transcode (ALAC/FLAC). nodejs = yt-dlp JS runtime for the
# SponsorBlock segment cut (--js-runtimes node).
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \n    ffmpeg nodejs && \n    ln -sf /usr/bin/nodejs /usr/local/bin/node && \n    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt gunicorn==22.0.0

COPY app.py /app/
COPY templates/ /app/templates/
COPY static/ /app/static/

RUN mkdir -p /data
VOLUME /data

EXPOSE 8080
CMD ["gunicorn", "--worker-class", "gthread", "--threads", "8", "--bind", "0.0.0.0:8080", "--timeout", "600", "app:app"]
