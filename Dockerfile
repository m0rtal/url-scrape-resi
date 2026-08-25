FROM python:3.11-slim-bookworm
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    chromium chromium-driver fonts-liberation libnss3 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    fastapi==0.115.0 'uvicorn[standard]==0.32.0' markdownify==0.14.1 httpx==0.27.2
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 3003
ENV PORT=3003
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3003", "--workers", "1"]
