FROM python:3.12.14-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace/backend
COPY backend/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
COPY backend /workspace/backend
COPY characters /workspace/characters
RUN useradd --create-home mediaos && chown -R mediaos:mediaos /workspace
USER mediaos
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
