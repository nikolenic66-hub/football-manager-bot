# Build the React Mini App in a Node stage so Node/npm are not installed in
# the production Python image.
FROM node:20-bookworm-slim AS miniapp-build
WORKDIR /build/mini_app
COPY mini_app/package.json ./package.json
COPY mini_app/ ./
RUN cd /build/mini_app && npm install && npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      fonts-dejavu-core supervisor \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-prod.txt ./requirements-prod.txt
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY . .
COPY --from=miniapp-build /build/mini_app/dist ./mini_app/dist
COPY supervisor.conf /app/supervisor.conf

EXPOSE 8000
CMD ["supervisord", "-c", "/app/supervisor.conf"]
