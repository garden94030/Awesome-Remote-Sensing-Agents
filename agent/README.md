# Local Remote-Sensing Analysis Agent (Gemini)

A CLI tool that analyses local satellite / aerial / UAV imagery using
Google Gemini's vision model.

## Setup

```bash
cd agent
pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste your key from https://aistudio.google.com/apikey
```

Optional for GeoTIFF metadata (CRS, bounds, resolution):

```bash
pip install rasterio
```

## Usage

```bash
# Structured scene description (JSON)
python rs_agent.py describe path/to/image.tif

# ESA WorldCover land-cover classification
python rs_agent.py classify path/to/image.jpg

# Object detection with custom target list
python rs_agent.py detect path/to/image.png --target "buildings,solar panels,ships"

# Change detection between two co-registered scenes
python rs_agent.py change before.tif after.tif

# Free-form custom prompt
python rs_agent.py custom path/to/image.tif \
  --prompt "Estimate the built-up ratio and list any water bodies."

# Batch a whole folder
python rs_agent.py batch ./scenes/ --task describe --glob '*.tif' -o results.json
```

Add `--output report.json` to write to a file, `--text` for plain-text
output, or `--model gemini-2.5-flash` for a faster/cheaper run.

## Supported formats

- JPG / PNG (any size — resized to max 3072 px edge)
- TIFF / GeoTIFF (Pillow handles most; rasterio adds geo metadata)

## How it works

1. Loads the image with Pillow, converts to RGB, downsamples if > 3072 px.
2. If it's a GeoTIFF and `rasterio` is installed, extracts CRS / bounds /
   resolution and passes them as context to the model.
3. Sends bytes + a task-specific prompt to Gemini 2.5 Pro with a
   remote-sensing system instruction.
4. Requests `response_mime_type=application/json` for structured tasks so
   the model returns strict JSON.

## Tasks

| Command | Output schema |
|--------|-------|
| `describe` | scene_type, dominant_land_cover, notable_features, resolution bucket, image quality issues, confidence, summary |
| `classify` | ESA WorldCover classes present (%), dominant class, heterogeneity, confidence |
| `detect` | per-target count / spatial distribution / regions / confidence |
| `change` | alignment quality, list of changes with magnitude & confidence, stable features, verdict |
| `custom` | free text |
| `batch` | array of `describe` / `classify` / `detect` results over a folder |
