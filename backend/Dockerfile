# One image for every cloud-side Python service (ingestor, migrations and
# the API); docker-compose picks the command.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Same code plus pytest; built only by `docker compose run --rm tests`.
FROM base AS test
RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["pytest", "-v"]

# Last stage = what a plain `docker build` produces: no test tooling.
FROM base AS runtime
