FROM python:3.12-slim

# Install system deps needed by asyncpg, grpc, cryptography, logging
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc \
      libpq-dev \
      libssl-dev \
      ca-certificates \
      jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first — this layer is cached unless requirements.txt changes
COPY adk/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY adk/          adk/
COPY governance-rules.yaml ./

# Create log directory
RUN mkdir -p /app/adk/logs /app/adk/briefs

# Cloud Run injects secrets as env vars at runtime.
# Never bake .env into the image.

# Set Python to unbuffered output (real-time Cloud Logging)
ENV PYTHONUNBUFFERED=1

# Run one PMO cycle and exit.
# Cloud Scheduler fires this job on your chosen interval.
CMD ["python", "-m", "adk.pmo_daemon", "--once"]
