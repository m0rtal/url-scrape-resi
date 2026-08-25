FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy
WORKDIR /app
# app.py itself uses stdlib http.server — no pip install needed
COPY app.py /app/app.py
# Need to bring deps in. Use pre-cached wheels via COPY wheels/ — 
# but we don't have wheels locally. Simplest: docker pip install.
# Since base image is large, pip install of 4 small wheels should fit memory.
RUN pip3 install --break-system-packages --no-cache-dir \
    fastapi==0.115.0 \
    'uvicorn[standard]==0.32.0' \
    markdownify==0.14.1 \
    httpx==0.27.2 2>&1 | tail -3
EXPOSE 3003
ENV PORT=3003
CMD ["python3", "app.py"]
