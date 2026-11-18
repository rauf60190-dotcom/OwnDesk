FROM node:24.11.1-bookworm-slim AS build
WORKDIR /app
RUN npm install -g pnpm@11.25.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build
FROM caddy:2.10.2-alpine
COPY --from=build /app/dist /srv
COPY infra/Caddyfile /etc/caddy/Caddyfile
