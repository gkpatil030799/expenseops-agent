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
COPY --chown=expenseops:expenseops scripts/bootstrap_database_roles.py ./scripts/bootstrap_database_roles.py
COPY --chown=expenseops:expenseops --from=frontend-build /frontend/dist ./app/static
RUN pip install --no-cache-dir --no-deps .

USER expenseops

EXPOSE 8000
CMD ["sh", "-c", "case \"${EXPENSEOPS_PROCESS:-}\" in web) exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} ;; outbox) exec python -m app.jobs.outbox ;; '') if [ \"${APP_ENV:-${ENVIRONMENT:-local}}\" = production ]; then echo 'EXPENSEOPS_PROCESS must be explicit in production' >&2; exit 64; fi; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} ;; *) echo 'Unsupported EXPENSEOPS_PROCESS' >&2; exit 64 ;; esac"]
