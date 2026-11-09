# syntax=docker/dockerfile:1
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# sandcorp-authkit은 공개 저장소 sandcorp-auth에서 git+https로 받는다(인증 없음, #320).
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
