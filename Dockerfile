# rebuild-2026-10-07
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2
WORKDIR /app
COPY requirements-prod.txt .
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements-prod.txt
COPY . .
CMD ["python","-m","app"]
