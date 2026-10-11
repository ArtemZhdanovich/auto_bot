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
# /api проксируется на backend-сервис compose-сети
RUN printf 'server { listen 80; location / { root /usr/share/nginx/html; try_files $uri /index.html; } location /api { proxy_pass http://backend:8000; } }\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
