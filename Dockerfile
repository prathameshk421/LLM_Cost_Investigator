# One image, one port: FastAPI serves the API and the built React UI.
FROM node:20-alpine AS ui
WORKDIR /ui
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/web:/app \
    LLMCI_WAREHOUSE=/app/data/warehouse.duckdb LLM_CALL_LOG=/app/data/logs/llm_calls.jsonl
WORKDIR /app
COPY pyproject.toml README.md ./
COPY llm_cost_investigator/ llm_cost_investigator/
RUN pip install --no-cache-dir -e ".[web]"
COPY web/api/ web/api/
COPY data/replay/ data/replay/
COPY data/reports/ data/reports/
COPY --from=ui /ui/dist web/frontend/dist
RUN useradd -m app && mkdir -p /app/data/logs && chown -R app /app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "3"]
