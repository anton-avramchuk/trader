# Production-сборка Angular за nginx. Контекст сборки — корень репозитория:
# docker compose build web. Для разработки с hot-reload: npx nx serve web.
FROM node:22 AS build

ENV NX_DAEMON=false CI=true
WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN npm ci

COPY nx.json tsconfig.base.json eslint.config.mjs ./
COPY libs/api-client libs/api-client
COPY apps/web apps/web
RUN npx nx build web

FROM nginx:1.27-alpine

ENV API_URL=http://api:8000
COPY apps/web/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/apps/web/browser /usr/share/nginx/html

EXPOSE 4200
