FROM python:3.13-alpine
RUN sed -i 's|dl-cdn.alpinelinux.org|dl-cdn.alpinelinux.org|' /etc/apk/repositories && echo @edge https://dl-cdn.alpinelinux.org/alpine/edge/main >> /etc/apk/repositories && apk add --no-cache chromium chromium-driver nss-tools
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 3003
ENV PORT=3003
CMD ["python3", "app.py"]
