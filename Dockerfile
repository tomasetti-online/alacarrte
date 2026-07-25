FROM python:3.12-slim
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg=7:* nodejs && ln -sf /usr/bin/nodejs /usr/local/bin/node && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir yt-dlp flask gunicorn==22.0.0
RUN mkdir -p /app /data
COPY app/app.py /app/
COPY app/templates/ /app/templates/
COPY app/static/ /app/static/
VOLUME /data
WORKDIR /app
EXPOSE 8080
CMD ["gunicorn", "--worker-class", "gthread", "--threads", "8", "--bind", "0.0.0.0:8080", "--timeout", "600", "app:app"]
