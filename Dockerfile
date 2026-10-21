FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[ui]" \
    && useradd --create-home --uid 1000 app

USER app
EXPOSE 8000

# Settings come from INVOICE_EXTRACT_* environment variables, e.g. INVOICE_EXTRACT_BASE_URL and INVOICE_EXTRACT_MODEL.
CMD ["invoice-extract", "serve", "--host", "0.0.0.0", "--port", "8000"]
