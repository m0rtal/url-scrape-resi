FROM alpine:3.20
# Use alpine community repo for chromium
RUN apk add --no-cache \
    chromium~=140 \
    nss-tools \
    libnss3 \
    libgbm \
    libasound2 \
    \
    python3 py3-pip \
    \
&& apk add --no-cache \
    --repository=https://dl-cdn.alpinelinux.org/alpine/v3.20/community \
    chromium 2>/dev/null || apk add --no-cache chromium

WORKDIR /app
COPY app.py /app/app.py
EXPOSE 3003
ENV PORT=3003
CMD ["python3", "app.py"]
