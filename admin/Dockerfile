FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM alpine:3.19
COPY --from=build /app/dist /dist
COPY --from=build /app/runtime-config.sh /runtime-config.sh
# 산출물 복사 + 실행 env(SENTRY_DSN·SENTRY_RELEASE)로 runtime-config.js 쓰기(#308)
CMD ["sh", "/runtime-config.sh", "/output"]
