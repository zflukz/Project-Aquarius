# Interface web app (React + Google Maps). Build context: repo root.
FROM node:20-alpine AS build
WORKDIR /app
COPY Interface/package.json Interface/package-lock.json ./
RUN npm ci
COPY Interface/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/interface/default.conf.template /etc/nginx/templates/default.conf.template
