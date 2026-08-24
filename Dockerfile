FROM node:22-slim AS web
WORKDIR /web
COPY dashboard/web/package.json dashboard/web/package-lock.json ./
RUN npm ci
COPY dashboard/web ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=4090

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt

COPY . .
COPY --from=web /web/dist /app/dashboard/web/dist
RUN mkdir -p input/images

EXPOSE 4090

CMD ["sh", "-c", "uvicorn dashboard.backend.app:app --host 0.0.0.0 --port ${PORT:-4090}"]
