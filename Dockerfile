# Use Python 3.11+
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt

# Install Playwright browsers (chromium only, for future use)
RUN playwright install chromium 2>/dev/null || true

# Copy project
COPY . .

# Create necessary directories
RUN mkdir -p dashboard_storage runtime output generated_images

# Expose port
EXPOSE 4090

# Run with uvicorn
CMD ["uvicorn", "dashboard.backend.app:app", "--host", "0.0.0.0", "--port", "4090"]
