# Инспектор ИИ — backend (REST API, очередь, аудит) + собранный интерфейс (React).
# Контекст сборки — папка с backend/ и frontend/ (см. docker-compose.yml).

# 1. Интерфейс
FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2. Backend
FROM node:24-slim
ENV NODE_ENV=production
WORKDIR /app/backend
COPY backend/package.json backend/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY backend/ ./
COPY --from=frontend /build/dist /app/frontend/dist
ENV PORT=8080 \
    DATA_DIR=/data \
    FRONTEND_DIST=/app/frontend/dist
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD node -e "fetch('http://localhost:8080/api/v1/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
CMD ["node", "--disable-warning=ExperimentalWarning", "src/server.js"]
