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

EXPOSE 8000

# entrypoint.py: Python garante stdout flush confiável (shell dash buffera).
# Loga cada passo (alembic, import de main, uvicorn) para diagnóstico claro.
CMD ["python", "-u", "entrypoint.py"]
