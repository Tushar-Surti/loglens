# Dashboard: Vite build, served by nginx which also proxies /api and /ws so the
# browser talks to a single origin (no CORS, no mixed-port websockets).
FROM node:20-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
# Same-origin defaults; nginx does the proxying.
ENV VITE_API_BASE="" \
    VITE_WS_BASE=""
RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
# 127.0.0.1, not localhost: inside the container `localhost` resolves to ::1
# first and nginx is listening on IPv4 only, which fails the probe on a page
# that is in fact serving fine.
HEALTHCHECK --interval=20s --timeout=3s --retries=5 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
