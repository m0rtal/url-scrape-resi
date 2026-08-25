FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy
WORKDIR /app
COPY app.py /app/app.py
RUN pip3 install --break-system-packages --no-cache-dir fastapi==0.115.0 'uvicorn[standard]==0.32.0' markdownify==0.14.1 httpx==0.27.2 2>&1 | tail -3
EXPOSE 3003
ENV PORT=3003
CMD ["python3", "app.py"]
