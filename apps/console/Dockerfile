FROM node:24.21.0-bookworm-slim AS build
WORKDIR /app
COPY apps/console/package*.json ./
RUN npm ci
COPY apps/console ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
FROM node:24.21.0-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
