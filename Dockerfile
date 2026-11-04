FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/app/data

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mtg_market_scraper ./mtg_market_scraper
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data && chown app /app/data

USER app
VOLUME /app/data
# One sync of today's Magic prices, then exit: non-zero when it still failed after its retries.
CMD ["python", "-m", "mtg_market_scraper", "sync"]
