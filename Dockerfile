FROM node:20.19.4-bookworm-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
COPY sandbox/frontend /sandbox/frontend
RUN npm run build

FROM python:3.11.13-slim-bookworm

RUN addgroup --system expenseops && adduser --system --ingroup expenseops expenseops

WORKDIR /app
COPY requirements.lock pyproject.toml README.md ./
RUN pip install --no-cache-dir --requirement requirements.lock
COPY --chown=expenseops:expenseops app ./app
COPY --chown=expenseops:expenseops sandbox ./sandbox
COPY --chown=expenseops:expenseops alembic ./alembic
COPY --chown=expenseops:expenseops alembic.ini ./
COPY --chown=expenseops:expenseops --from=frontend-build /frontend/dist ./app/static
RUN pip install --no-cache-dir --no-deps .

USER expenseops

EXPOSE 8000
CMD ["sh", "-c", "if [ \"${EXPENSEOPS_PROCESS:-web}\" = \"outbox\" ]; then exec python -m app.jobs.outbox; else exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}; fi"]
