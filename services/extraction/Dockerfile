FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgeos-dev \
    libproj-dev \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY . /app/services/extraction
COPY ../../libs /app/libs

RUN pip install --no-cache-dir -e /app/libs/observability \
    -e /app/libs/queue \
    -e /app/libs/storage \
    -e /app/libs/outbox \
    -e /app/libs/envelope \
    -e /app/libs/masking \
    -e /app/libs/audit \
    -e /app/libs/config_client \
    -e /app/services/extraction

EXPOSE 8000

CMD ["uvicorn", "extraction.main:app", "--host", "0.0.0.0", "--port", "8000"]
