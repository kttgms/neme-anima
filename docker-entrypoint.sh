#!/usr/bin/env bash
set -e

# Setup models and data directories
export MODELS_DIR="${MODELS_DIR:-/models}"
export DIFFUSION_PIPE_DIR="${DIFFUSION_PIPE_DIR:-/app/diffusion-pipe}"

# Generate the training defaults config for the UI
DEFAULTS_DIR="$HOME/.neme-anima"
DEFAULTS_FILE="$DEFAULTS_DIR/training-defaults.json"
mkdir -p "$DEFAULTS_DIR"

cat > "$DEFAULTS_FILE" <<JSON
{
  "diffusion_pipe_dir": "$DIFFUSION_PIPE_DIR",
  "dit_path":           "$MODELS_DIR/anima-base-v1.0.safetensors",
  "vae_path":           "$MODELS_DIR/qwen_image_vae.safetensors",
  "llm_path":           "$MODELS_DIR/qwen_3_06b_base.safetensors"
}
JSON

# If arguments are provided (e.g. bash, or specific commands), run them instead
if [ $# -gt 0 ]; then
    exec "$@"
else
    # Default to running the UI
    # Listen on 0.0.0.0 so it's accessible outside the container
    exec uv run neme-anima ui --host 0.0.0.0 --port 8000
fi
