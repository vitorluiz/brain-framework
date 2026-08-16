FROM python:3.11-slim

WORKDIR /app

# Dependências de build leves + runtime
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[postgres]"

# Entrada padrão: worker Celery (sobrescreva com `brain`/`brain-tool` se quiser)
CMD ["brain-worker"]
