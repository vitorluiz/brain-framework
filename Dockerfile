FROM python:3.11-slim

WORKDIR /app

# Usuário não-root para o worker (auditoria §6.7) — sem privilégios no container.
RUN groupadd -g 10001 brain \
    && useradd -u 10001 -g brain -m brain

# Dependências de build leves + runtime
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[postgres]"

# Garante que o usuário não-root consiga ler o código e escrever em HOME (chave
# Ed25519 auto-gerada vive em $BRAIN_ROOT/.signing/, default ~/.hermes/brain).
RUN chown -R brain:brain /app

USER brain

# Entrada padrão: worker Celery (sobrescreva com `brain`/`brain-tool` se quiser)
CMD ["brain-worker"]
