FROM mcr.microsoft.com/playwright:v1.55.0-jammy

WORKDIR /app

# Install only production deps. Chromium is already in the base image at /ms-playwright/.
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund --loglevel=error

COPY server.js proxy.js ./

ENV PORT=3003 \
    NODE_ENV=production \
    LOG_LEVEL=info

EXPOSE 3003

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD node -e "require('http').get('http://127.0.0.1:3003/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))" || exit 1

# Run as root (Portainer LXC userns mapping doesn't have a 'node' user)
USER root

CMD ["node", "server.js"]
