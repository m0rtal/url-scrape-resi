FROM python:3.11-slim

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    chromium chromium-driver fonts-liberation libgbm1 libnss3 libxss1 libasound2t64 \
    && rm -rf /var/lib/apt/lists/*

# Chromium is at /usr/bin/chromium
# Install playwright (matching browser)
RUN pip install --break-system-packages --no-cache-dir \
    playwright==1.55.0 \
    fastapi==0.115.0 \
    'uvicorn[standard]==0.32.0' \
    markdownify==0.14.1 \
    httpx==0.27.2

# Verify chromium and python playwright
RUN which chromium && chromium --version
RUN python3 -c "from playwright.sync_api import sync_playwright; print('playwright OK')"

WORKDIR /app
EXPOSE 3003

COPY app.py /app/app.py
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/lib/chromium

CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3003", "--workers", "1"]
