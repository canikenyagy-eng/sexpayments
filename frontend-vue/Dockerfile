# Этап сборки (Build Stage)
FROM node:20-alpine as build-stage

WORKDIR /app

# Копируем файлы зависимостей
COPY package*.json ./

# Устанавливаем зависимости
RUN npm install

# Копируем весь код фронтенда
COPY . .

ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL

ARG VITE_SENTRY_DSN=""
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN

# Собираем проект для продакшена
RUN npm run build

# Этап выполнения (Production Stage)
FROM nginx:stable-alpine as production-stage

# Копируем собранные файлы из этапа сборки
COPY --from=build-stage /app/dist /usr/share/nginx/html

# Копируем конфигурацию Nginx (создадим её позже)
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
