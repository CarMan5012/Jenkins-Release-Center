# ==========================================
# Phase 1: Build Frontend Vue Application
# ==========================================
FROM node:24-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ==========================================
# Phase 2: Build Python dependencies (Wheel compiler)
# ==========================================
FROM python:3.12-slim AS python-builder
WORKDIR /build

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlcipher-dev \
    python3-dev

COPY backend/requirements.txt ./
# Compile wheels for requirements (including sqlcipher3-binary compilation details)
RUN pip wheel --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --wheel-dir=/build/wheels -r requirements.txt

# ==========================================
# Phase 3: Final Slim Production Runner
# ==========================================
FROM python:3.12-slim AS runner
WORKDIR /app

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources
# Install only sqlcipher runtime dynamic libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlcipher \
    libsqlcipher-dev \
    && rm -rf /var/lib/apt/lists/*

# Install compiled Python packages from Phase 2 wheels
COPY --from=python-builder /build/wheels /app/wheels
RUN pip install --no-cache-dir /app/wheels/* && rm -rf /app/wheels

# Copy Vue statically compiled distribution directory
COPY --from=frontend-builder /build/dist /app/dist

# Copy backend source directory
COPY backend/ ./

# Expose release scheduler port
EXPOSE 8001

ENV TZ=Asia/Shanghai

# Run application as a non-privileged system user
RUN useradd -u 10001 -U appuser && chown -R appuser:appuser /app
USER appuser

# Start application via single worker Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
