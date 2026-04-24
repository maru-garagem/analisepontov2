FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependências de sistema: Tesseract (OCR) com pacote de língua portuguesa
# e Poppler (renderização de PDF usada por pdf2image).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-por \
        poppler-utils \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

# entrypoint.sh loga cada passo (migration, import, uvicorn) para facilitar
# diagnosticar crashes silenciosos no container.
CMD ["/app/entrypoint.sh"]
