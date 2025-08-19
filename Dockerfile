# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (optional minimal)
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy app
COPY PyAutoBot.py ./
COPY db.py ./
COPY models.py ./
COPY crud.py ./
COPY stripe_handlers.py ./
COPY README.md ./

# You can mount a volume for the database if using SQLite
# VOLUME ["/app/data"]

EXPOSE 8080

# Default to production mode (webhook)
ENV LOCAL_POLLING=0

CMD ["uvicorn", "PyAutoBot:app", "--host", "0.0.0.0", "--port", "8080"]
