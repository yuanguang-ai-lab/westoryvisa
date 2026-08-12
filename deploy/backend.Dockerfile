FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCFLOW_BACKEND_HOST=0.0.0.0 \
    DOCFLOW_BACKEND_PORT=4176

WORKDIR /app

COPY backend /app/backend
COPY school_directory_verified.json /app/school_directory_verified.json

RUN addgroup --system docflow \
    && adduser --system --ingroup docflow --home /app docflow \
    && mkdir -p /app/data/uploads \
    && chown -R docflow:docflow /app

USER docflow

EXPOSE 4176

CMD ["python", "-m", "backend.main", "4176"]
