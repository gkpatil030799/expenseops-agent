FROM node:20-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
COPY sandbox/frontend /sandbox/frontend
RUN npm run build

FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY sandbox ./sandbox
COPY alembic ./alembic
COPY alembic.ini ./
COPY --from=frontend-build /frontend/dist ./app/static
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
