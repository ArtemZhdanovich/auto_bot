# Mini App (этап 5): сборка Vite → статика через nginx
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
# API-URL бэкенда передаётся через аргумент (в проде за nginx/CDN)
ARG VITE_API_BASE=/api
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# HTTPS (self-signed, генерируется в entrypoint) + проксирование /api на backend
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/entrypoint-ssl.sh /entrypoint-ssl.sh
RUN chmod +x /entrypoint-ssl.sh && apk add --no-cache openssl
ENV NG_HOST_IP=127.0.0.1
EXPOSE 80 443
ENTRYPOINT ["/entrypoint-ssl.sh"]
CMD ["nginx", "-g", "daemon off;"]
