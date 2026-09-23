# syntax=docker/dockerfile:1
#
# Runtime image for the extraction service.
#   docker build -t email-intent .
#   docker run --rm -p 8080:8080 --env-file .env email-intent
#   docker run --rm email-intent email-intent --demo --format table
#
# The application has no third-party runtime dependencies, so the final image
# is the slim Python base plus this package.

FROM python:3.14-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip build \
    && python -m build --wheel --outdir /dist


FROM python:3.14-slim

LABEL org.opencontainers.image.title="email-intent" \
      org.opencontainers.image.description="Turn unstructured customer emails into structured purchasing requirements." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/rishab-sharma/email-intent"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

USER appuser
WORKDIR /home/appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:' + os.environ.get('PORT', '8080') + '/health', timeout=2).status == 200 else 1)"

# Webhook intake by default. Override the command for the CLI or watch mode.
CMD ["sh", "-c", "email-intent-server --serve ${PORT}"]
