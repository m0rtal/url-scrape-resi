FROM python:3.13-alpine
RUN apk add --no-cache chromium chromium-driver
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 3003
ENV PORT=3003
ENV DEBIAN_FRONTEND=noninteractive
CMD ["python3", "app.py"]
