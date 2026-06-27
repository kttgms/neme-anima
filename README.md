# What is the fork for?

This is a strictly personal fork driven entirely by my own preferences. It runs in a Docker container, executes the extraction process independently to reduce memory leaks, and allows the number of workers to be configured arbitrarily based on machine performance.

# Neme-Anima

A three-step character LoRA builder:

1. Extract crops of one or more characters from a video using reference images.
2. Auto-tag each crop with WD14 danbooru tags and natural-language captions, then reorganize the dataset from the UI.
3. Train a LoRA per character on Anima with the parameters already wired in.

The extractor and tagger are model-agnostic and produce output sized for kohya-ss / OneTrainer / sd-scripts on SDXL-class anime models (Pony, Illustrious, NoobAI, vanilla SDXL). The trainer targets Anima.

<p align="center"><img src="docs/chie.png" alt="Result with a Chie LoRA applied to Anima" width="50%"></p>

## Pipeline

For each video:

1. PySceneDetect splits it into shots.
2. DeepGHS YOLO (via `imgutils`) detects characters per frame.
3. ByteTrack links detections into per-shot tracklets.
4. CCIP matches tracklets to each character's reference images and assigns tracklets to whichever character scores best (or rejects them if no character matches).
5. 1–3 frames per kept tracklet are picked by sharpness, visibility, and aspect ratio.
6. Each pick is cropped at longest-side 1024 with the original background.
7. CCIP runs over the kept crops a second time to drop near-duplicates inside a sliding window.
8. WD14 EVA02-Large v3 writes a kohya-style `.txt` next to each `.png`.

Detections and tracklets are cached so threshold re-runs skip the slow stages.

## Requirements

System packages (`install_and_run.sh` installs these via apt on Debian/Ubuntu/WSL2; install manually on other distros):

- `ffmpeg` (used by the UI for video thumbnails, segment previews, and probing)
- `git` (used to clone the trainer)

For the extractor/tagger:

- NVIDIA GPU, 4 GB VRAM minimum, 8 GB comfortable

For the trainer:

- Linux / WSL2 with CUDA 12.4+
- NVIDIA GPU, 6 GB VRAM minimum, 18 GB for full res LoRA

## One-click install and run

```sh
git clone https://github.com/negaga53/neme-anima.git
cd neme-anima
bash install_and_run.sh
```

The script installs `uv` and Node.js if they aren't already on the system, syncs the Python deps, builds the frontend, clones `tdrussell/diffusion-pipe` into `~/diffusion-pipe` and sets up its Python 3.12 venv, downloads the three Anima training weights (~14 GB) from HuggingFace, prefills the four trainer paths in the UI's Settings tab, downloads the danbooru tag list used for tag autocomplete, and starts the server.

Re-running it is safe (will skip anything already in place). To update an existing one-click install, run `git pull --ff-only` and then run `bash install_and_run.sh` again; the script rebuilds the web UI.

Useful environment overrides:

| Variable | Default | What it does |
|---|---|---|
| `DIFFUSION_PIPE_DIR` | `~/diffusion-pipe` | Where to clone diffusion-pipe |
| `DIFFUSION_PIPE_PYTHON` | `3.12` | Python interpreter/version for diffusion-pipe's venv |
| `MODELS_DIR` | `~/.cache/neme-anima/models` | Where to put the downloaded weights |
| `SKIP_MODELS=1` | off | Skip the 14 GB weight download |
| `SKIP_TAGS=1` | off | Skip the danbooru tag-list download (autocomplete) |
| `SKIP_LAUNCH=1` | off | Install everything, but don't start the UI at the end |

Linux / WSL2 only. On Linux, Node.js is installed through apt (with sudo) when available, and through nvm otherwise.

## Manual install / After updating

```sh
uv sync --group gpu
```

First run downloads ~2.8 GB of weights (anime YOLOv8 person + face, CCIP, isnetis/anime-seg, WD14 with embeddings, CLIP base) to `~/.cache/huggingface/hub/`.

Download the danbooru tag list used by the tag-editor autocomplete (one-time, ~5 MB into `~/.neme-anima/`):

```sh
uv run neme-anima tags fetch
```

## CLI

```sh
uv run neme-anima project create ~/neme-projects/megumin --name megumin
uv run neme-anima project add-ref ~/neme-projects/megumin /path/to/portrait.png
uv run neme-anima project add-video ~/neme-projects/megumin /path/to/ep01.mkv
uv run neme-anima project add-video ~/neme-projects/megumin /path/to/ep02.mkv
uv run neme-anima project extract ~/neme-projects/megumin
```

Project folder layout:

```
~/neme-projects/megumin/
  project.json
  refs/
  output/
    kept/             ep01__s003_t012_f000847.png + .txt
    rejected/
    metadata.jsonl
    cache/<stem>/     scenes.parquet, tracklets.parquet
```

Re-run with new thresholds (skips detection + tracking):

```sh
uv run neme-anima project rerun ~/neme-projects/megumin --video ep01
```

## Web UI

After cloning the repository, or after `git pull` when updating, install/update the frontend dependencies and rebuild the static UI bundle:

```sh
cd frontend && npm install && npm run build && cd ..
```

`git pull` alone can leave you running the old UI because the built files in `src/neme_anima/server/static/` are generated locally and not committed.

Then start the server:

```sh
uv run neme-anima ui
```

Binds to `127.0.0.1:<random-port>` and opens the SPA. Tabs: Sources, Frames, Training, Settings.

### Sources

Add MKV/MP4 videos and reference images.

![Sources tab](docs/neme-anima_extract.png)

### Frames

- Add or remove images from the dataset (using drag&drop).
- Edit tags inline by clicking a pill; edit the natural-language description in the same panel.
- Search across the dataset by tag.
- Bulk-edit tags with regex replace, with live preview.
- Re-crop any image.
- Filter by character with the chips at the top of the tab. Move a frame to a different character or also-assign it to a second character.

Selection: shift-click ranges, ctrl-click multi-toggle, `A` select all, `D` / `Esc` deselect. Hover a thumbnail for the tag overlay.

![Frames tab](docs/neme-anima_frames.png)

### Training

LoRA training with stop/resume and checkpoint retention. Targets Anima. One LoRA per character, queued sequentially.

Two per-character knobs sit alongside the standard rank/alpha/lr settings:

- Core-tag pruning. Compute the tags that show up in more than X% of a character's frames (default 35%) and drop them from the captions at staging time. Turns "long hair, blue eyes, school uniform, smile, ..." into just "smile, ..." for a character whose hair, eyes, and outfit are constants. The LoRA learns those from the visuals; the caption only adds noise. Off by default; opt in once you've reviewed the suggested list.
- Repeat multiplier. Over- or under-sample a character's frames in the dataset. `0.0` is auto, computed from relative frame counts so a 50-frame character isn't drowned by a 500-frame one. A positive value pins it manually.

![Training tab](docs/neme-anima_train.png)

Training is run through [tdrussell/diffusion-pipe](https://github.com/tdrussell/diffusion-pipe), which has to be set up separately:

```sh
git clone https://github.com/tdrussell/diffusion-pipe ~/diffusion-pipe
cd ~/diffusion-pipe
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
```

Then in the Settings tab, point `diffusion_pipe_dir` at that clone and set the Anima DiT, Qwen VAE, and Qwen 3 0.6B text encoder paths (separate download on Huggingface).

### Settings

Per-project threshold overrides (frame stride, identification distance, crop padding, etc.).

Project state lives in the project folder. The only server-side file is `~/.neme-anima/db.sqlite` (project registry).

## Docker Deployment

You can run Neme-Anima in a Docker container, avoiding the need to install dependencies directly on your host machine.

### 1. Build the image

```sh
docker build -t neme-anima .
```

This multi-stage build will compile the frontend and prepare a Python 3.12 environment with `uv`, `ffmpeg`, and `diffusion-pipe` built-in. 

### 2. Run the container

To ensure the container can leverage your GPU, access your downloaded model weights, and store project data persistently, run the container with NVIDIA runtime and volume mounts:

```sh
docker run --gpus all \
  -v ~/.cache/neme-anima/models:/models \
  -v ~/neme-projects:/data \
  -p 8000:8000 \
  -it neme-anima
```

- `--gpus all`: Exposes your host's NVIDIA GPU to the container.
- `-v ~/.cache/neme-anima/models:/models`: Mounts the 14GB Anima model weights so they do not need to be baked into the image.
- `-v ~/neme-projects:/data`: Mounts a persistent host directory to the container's `/data` folder. Your UI settings, sqlite DB, and projects will be safely stored here.
- `-p 8000:8000`: Forwards the Neme-Anima UI to `http://localhost:8000`.

The container will automatically point the training defaults to the mounted `/models` folder and store UI state in `/data/.neme-anima/`.
