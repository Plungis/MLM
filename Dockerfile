FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MLM_CONFIG_FILE=/config/config.toml \
    MLM_DB_FILE=/data/data.sqlite3

WORKDIR /app
COPY python/pyproject.toml python/README.md /app/
COPY python/src /app/src
RUN pip install --no-cache-dir .

VOLUME ["/config", "/data"]
EXPOSE 3157

CMD ["sh", "-c", "mlm-python run --config \"$MLM_CONFIG_FILE\" --database \"$MLM_DB_FILE\""]
