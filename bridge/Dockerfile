# Dorg MCP bridge — generic static image.
# Per-competency files: competency.manifest.json, DOCUMENTATION.md, competency_handlers.py (optional).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    MANIFEST_PATH=competency.manifest.json \
    HANDLERS_MODULE=competency_handlers

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY server.py manifest.py competency_handlers.py ./
COPY handlers/ ./handlers/
COPY competency.manifest.json DOCUMENTATION.md ./

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
url='http://127.0.0.1:'+os.getenv('PORT','8080')+'/health'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=4).status==200 else 1)" || exit 1

CMD uvicorn server:app --host 0.0.0.0 --port ${PORT}
