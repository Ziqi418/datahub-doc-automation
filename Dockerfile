FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir uv
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --extra datahub
COPY backend ./backend
COPY scripts ./scripts
COPY demo ./demo
ENV DATABASE_PATH=/data/document_enrichment.db
EXPOSE 8000
CMD ["uv", "run", "--directory", "backend", "--extra", "datahub", "uvicorn", "document_enrichment.api.app:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
