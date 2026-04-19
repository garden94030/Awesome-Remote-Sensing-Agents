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
| `grayzone` | maritime / infrastructure / NDVI-NDWI indicators of gray-zone coercion, with benign explanations and confidence per finding |

## Gray-zone analysis workflow (for small scenes with NDVI / NDWI)

If your scenes are small and you have already computed NDVI / NDWI in QGIS:

1. In QGIS, for each scene, export three rendered PNGs at the **same extent**:
   - `rgb.png`   — true-colour composite
   - `ndvi.png`  — NDVI styled with a colour ramp
   - `ndwi.png`  — NDWI styled with a colour ramp
   (Project → Import/Export → Export Map to Image, or right-click layer →
   Export → Save As → Rendered image, with "Map canvas extent" + a modest
   resolution like 2048 px wide. Small is fine — Gemini sees everything.)

2. Run:

   ```bash
   python rs_agent.py grayzone \
     --rgb  path\to\rgb.png \
     --ndvi path\to\ndvi.png \
     --ndwi path\to\ndwi.png \
     --context "金門本島西側海岸, 2026年3月, Pleiades 0.5 m" \
     -o kinmen_grayzone.json
   ```

3. The more specific the `--context` string, the sharper the analysis.
   Include: rough location, date, sensor name, ground sampling distance,
   and any known civilian features you want the model to treat as benign
   baseline (e.g. "a civilian port sits at south-west of the frame").

4. Extra layers are supported via `--extra LABEL=PATH`, e.g.
   `--extra sar=./sar.png --extra thermal=./thermal.png`.

Output is structured JSON with three categories — maritime_indicators,
infrastructure_indicators, environmental_index_findings — each with
visual evidence, **benign explanations**, concern level, and confidence.
