# Veilfile: Django + DRF in one container. The site has no build step: templates, CSS and JS are served as they are.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=False
# Unprivileged runtime user (gunicorn, migrate and the sweeper never need root).
RUN groupadd --system --gid 10001 veilfile \
    && useradd --system --uid 10001 --gid veilfile --create-home veilfile
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY docs/llms.txt /app/docs/llms.txt
# Render does not expose service env vars to Docker builds, so settings can't
# see the real DJANGO_SECRET_KEY or DATABASE_URL here. collectstatic only
# needs settings to import and never opens the database — use a dummy secret
# and allow the SQLite placeholder; the real values apply at runtime.
RUN DJANGO_SECRET_KEY=docker-build-only-dummy DJANGO_ALLOW_SQLITE=True \
    python manage.py collectstatic --noinput
# Code and collected static files stay root-owned (read-only to the app);
# only the local-storage dir (DROP_STORAGE=local) is writable.
RUN chmod +x /app/backend/entrypoint.sh \
    && mkdir -p /app/backend/media \
    && chown veilfile:veilfile /app/backend/media
USER veilfile
EXPOSE 8000
CMD ["/app/backend/entrypoint.sh"]
