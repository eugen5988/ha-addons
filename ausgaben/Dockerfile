FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Berlin
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ausgaben

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY config.yaml CHANGELOG.md ./

CMD ["python", "-m", "app.main"]
