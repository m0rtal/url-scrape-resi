FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy
WORKDIR /app

# Bring deps in. The playwright/python image ships with Chromium 1187 + Python 3.10 + pip,
# so we only need the small Python deps. We bake them at build time to avoid pip install
# at container startup (which was the slow path that made redeploys take minutes).

# Install pip via get-pip if not present (some variants lack it).
RUN if ! command -v pip3 >/dev/null 2>&1; then \
      curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && \
      python3 /tmp/get-pip.py --break-system-packages 2>&1 | tail -1; \
    fi

# Install minimal Python deps for FastAPI server
RUN pip3 install --break-system-packages --no-cache-dir --quiet \
    fastapi==0.115.0 \
    'uvicorn[standard]==0.32.0' \
    markdownify==0.14.1 \
    httpx==0.27.2 2>&1 | tail -2

# Bake app.py into image — version 2.0.0 with proper virtual-time-budget + retry + uvicorn
COPY app.py /app/app.py
RUN chmod 644 /app/app.py

EXPOSE 3003
ENV PORT=3003 \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

CMD ["python3", "/app/app.py"]