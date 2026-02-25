FROM python:3.10-slim

RUN apt-get update && apt-get install -y build-essential && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py index_config.py ./
COPY sql ./sql
COPY config ./config
COPY config/indices.json /config/indices.json

USER appuser

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
