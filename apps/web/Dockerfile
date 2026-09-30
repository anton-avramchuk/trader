# Dev-сервер Angular. Контекст сборки — корень репозитория: docker compose build web
FROM node:22

ENV NX_DAEMON=false CI=true
WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN npm ci

COPY nx.json tsconfig.base.json eslint.config.mjs ./
COPY libs/api-client libs/api-client
COPY apps/web apps/web

EXPOSE 4200
CMD ["npx", "nx", "serve", "web", "--host=0.0.0.0"]
