# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt requirements-ocr.txt requirements-index.txt requirements-agent.txt ./

# 预构建 wheel，运行镜像无需编译器或联网安装依赖。
RUN python -m pip wheel --wheel-dir /wheels \
    -r requirements.txt \
    -r requirements-ocr.txt \
    -r requirements-index.txt \
    -r requirements-agent.txt


FROM python:3.12-slim AS runtime

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

# libgomp/libglib/libgl 是 ONNX Runtime 及 OCR 图像处理常见的运行时依赖。
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get \
        -o Acquire::Retries=10 \
        -o Acquire::https::Timeout=60 \
        update \
    && apt-get \
        -o Acquire::Retries=10 \
        -o Acquire::https::Timeout=60 \
        install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home app

COPY --from=builder /wheels /wheels
COPY requirements.txt requirements-ocr.txt requirements-index.txt requirements-agent.txt /tmp/requirements/

RUN python -m pip install --no-index --find-links=/wheels \
        -r /tmp/requirements/requirements.txt \
        -r /tmp/requirements/requirements-ocr.txt \
        -r /tmp/requirements/requirements-index.txt \
        -r /tmp/requirements/requirements-agent.txt \
    && rm -rf /wheels /tmp/requirements

WORKDIR /app

COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app evaluation ./evaluation
COPY --chown=app:app pyproject.toml README.md ./

RUN mkdir -p data/raw data/pages data/ocr data/processed data/indexes \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
