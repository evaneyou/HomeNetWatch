# Base images are pinned by digest; Dependabot (docker) proposes new digests.
FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS build
WORKDIR /app
ARG VITE_API_BASE_URL=http://127.0.0.1:8847
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
# npm ci installs exactly what package-lock.json lists, with its integrity hashes.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202
WORKDIR /app
# The static file server is pinned in serve/package-lock.json too.
COPY serve/package.json serve/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
EXPOSE 3847
CMD ["node_modules/.bin/serve", "-s", "dist", "-l", "3847"]
