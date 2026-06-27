# syntax=docker/dockerfile:1.4

# ==============================================================================
# Stage 1: Frontend Build
# ==============================================================================
FROM node:20-slim AS frontend-build

WORKDIR /app/frontend

# Copy frontend source code
COPY frontend/ ./

# Install dependencies and build
RUN npm install && npm run build

# ==============================================================================
# Stage 2: Main Application
# ==============================================================================
FROM python:3.12-slim

# Prevent prompts during apt-get install and prevent python from writing pyc
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
# ffmpeg: required for video thumbnails and processing
# git: required to clone diffusion-pipe
# build-essential: provides gcc, required for PyTorch Triton (CUDA kernel compilation)
# curl: required to download uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package installer)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

WORKDIR /app

# Copy python dependencies definitions
COPY pyproject.toml uv.lock ./
COPY README.md ./

# Copy application source code
COPY src/ ./src/

# Install python dependencies with GPU support into the system python
# We use --frozen to ensure uv.lock is strictly respected
RUN uv sync --frozen --group gpu

# Copy built frontend assets from the frontend-build stage
COPY --from=frontend-build /app/src/neme_anima/server/static/ ./src/neme_anima/server/static/

# Clone diffusion-pipe and set up its specific venv
# diffusion-pipe needs its own venv because of conflicting dependency constraints.
RUN git clone --recurse-submodules https://github.com/tdrussell/diffusion-pipe /app/diffusion-pipe && \
    cd /app/diffusion-pipe && \
    uv venv --python 3.12 && \
    uv pip install --python .venv/bin/python -r requirements.txt && \
    uv pip install --python .venv/bin/python torchvision deepspeed

# Prepare Docker entrypoint
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Environment variables to inform the app where volumes will be mounted
ENV DIFFUSION_PIPE_DIR="/app/diffusion-pipe"
ENV MODELS_DIR="/models"
# Configure Neme-Anima to store its own configurations in /data/.neme-anima
ENV HOME="/data"

# Expose default UI port
EXPOSE 8000

# Set the default entrypoint
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
