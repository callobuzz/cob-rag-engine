FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyPI occasionally serves an index page slowly; pip's 15s default then fails the
# whole build with a misleading "dependency conflict". Be patient instead.
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10

COPY pyproject.toml .
RUN pip install --no-cache-dir . && \
    playwright install chromium

COPY scraper/ scraper/

EXPOSE 8001

CMD ["uvicorn", "scraper.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
