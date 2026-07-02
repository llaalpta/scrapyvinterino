FROM node:22-alpine AS build

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile=false

COPY . .
RUN pnpm build

FROM nginx:1.29-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
