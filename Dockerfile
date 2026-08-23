FROM python:3.12-slim

ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .

ADD https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.deb /tmp/litestream.deb
RUN dpkg -i /tmp/litestream.deb && rm /tmp/litestream.deb

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY scripts ./scripts
COPY litestream.yml /etc/litestream.yml

# The runtime drops to this user (the entrypoint re-execs itself after
# fixing volume ownership — fly volumes mount root-owned). Code stays
# root-owned read-only; only the data locations become writable.
RUN useradd --create-home --uid 1000 app

EXPOSE 8000

# Boot order lives in scripts/docker-entrypoint.sh: litestream restore +
# replicate only when BUCKET_NAME is set (fly.io + `fly storage create`;
# plain `docker run` skips litestream), then alembic migrations, then a
# single-worker uvicorn. --proxy-headers lets uvicorn honor X-Forwarded-*
# from Fly's edge proxy.
ENV PYTHONPATH=/app

CMD ["scripts/docker-entrypoint.sh"]
