FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System dependencies:
#  - tesseract + ocrmypdf : OCR for scanned PDFs/images
#  - poppler-utils        : pdftotext (conversion-fidelity check)
#  - libreoffice-*        : ODT/ODS/ODP + legacy DOC/PPT/RTF conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
      ocrmypdf poppler-utils \
      libreoffice-writer libreoffice-calc libreoffice-impress \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --system --home-dir /app --shell /usr/sbin/nologin mdweb \
 && mkdir -p /app/data && chown -R mdweb:mdweb /app/data
USER mdweb

ENV DATA_DIR=/app/data \
    TIKTOKEN_CACHE_DIR=/app/data/tiktoken \
    PYBIN=python3

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
