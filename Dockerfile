FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATA_DIR=/tmp/data TESSDATA_DIR=/app/tessdata PORT=10000 \
    OMP_THREAD_LIMIT=1

# libs mínimas para OpenCV headless / tesserocr (wheel con Tesseract 5 incluido)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modelos de Tesseract (francés, inglés y detección de orientación)
RUN mkdir -p /app/tessdata && for l in eng fra osd; do \
      curl -fsSL -o /app/tessdata/$l.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/$l.traineddata; \
    done

COPY app ./app
WORKDIR /app/app
EXPOSE 10000
CMD ["sh", "-c", "mkdir -p $DATA_DIR && exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
