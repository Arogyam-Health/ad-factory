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
RUN mkdir -p dashboard_storage runtime output generated_images input/images

EXPOSE 4090

CMD ["sh", "-c", "uvicorn dashboard.backend.app:app --host 0.0.0.0 --port ${PORT:-4090}"]
