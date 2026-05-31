FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsndfile1 libfftw3-dev libyaml-dev \
    libsamplerate0 libtag1-dev \
    ca-certificates curl wget \
    ffmpeg \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Essentia (audio feature extraction) — installs cleanly from PyPI
RUN pip install --no-cache-dir essentia

COPY app/ ./app/

CMD ["celery", "-A", "app.tasks.celery_app", "worker", "--loglevel=INFO"]
