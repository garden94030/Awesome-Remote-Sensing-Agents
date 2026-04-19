"""Local remote-sensing analysis agent powered by Google Gemini.

Reads satellite / aerial imagery from disk and asks Gemini (vision)
to produce structured analysis: description, land-cover classification,
object detection, change detection, or a custom task.

Usage:
    python rs_agent.py describe  path/to/image.tif
    python rs_agent.py classify  path/to/image.jpg
    python rs_agent.py detect    path/to/image.png --target "buildings,roads,vehicles"
    python rs_agent.py change    before.tif after.tif
    python rs_agent.py custom    path/to/image.tif --prompt "Estimate built-up ratio"
    python rs_agent.py batch     ./scenes/ --task describe --glob '*.tif'

Requires GEMINI_API_KEY (or GOOGLE_API_KEY) in env or .env file.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image

try:
    from google import genai
    from google.genai import types
except ImportError:
    sys.stderr.write(
        "Missing dependency: install with `pip install -r requirements.txt`\n"
    )
    raise


MAX_EDGE = 3072
JPEG_QUALITY = 92
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")


@dataclass
class LoadedImage:
    path: Path
    mime_type: str
    data: bytes
    original_size: tuple[int, int]
    sent_size: tuple[int, int]
    geo_meta: dict | None = None


def _load_geotiff_meta(path: Path) -> dict | None:
    """Best-effort GeoTIFF metadata extraction via rasterio if available."""
    try:
        import rasterio
    except ImportError:
        return None
    try:
        with rasterio.open(path) as ds:
            bounds = ds.bounds
            return {
                "driver": ds.driver,
                "crs": str(ds.crs) if ds.crs else None,
                "width": ds.width,
                "height": ds.height,
                "count": ds.count,
                "dtype": str(ds.dtypes[0]) if ds.dtypes else None,
                "bounds": {
                    "left": bounds.left,
                    "bottom": bounds.bottom,
                    "right": bounds.right,
                    "top": bounds.top,
                },
                "resolution": ds.res,
            }
    except Exception:
        return None


def load_image(path: Path) -> LoadedImage:
    if not path.exists():
        raise FileNotFoundError(path)

    geo_meta = None
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        geo_meta = _load_geotiff_meta(path)

    img = Image.open(path)
    img.load()
    original_size = img.size

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > MAX_EDGE:
        scale = MAX_EDGE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    data = buf.getvalue()

    return LoadedImage(
        path=path,
        mime_type="image/jpeg",
        data=data,
        original_size=original_size,
        sent_size=img.size,
        geo_meta=geo_meta,
    )


SYSTEM_INSTRUCTION = """You are an expert remote-sensing analyst.
You read satellite, aerial, and UAV imagery and produce precise, structured
interpretations. You always:

- Ground every claim in visible evidence in the image.
- Use standard land-cover vocabulary (e.g., Anderson Level II, CORINE, or
  ESA WorldCover categories) where appropriate.
- Qualify uncertainty explicitly when spatial resolution, illumination,
  cloud cover, or occlusion limits interpretation.
- Prefer quantitative estimates (approximate %, counts, extents) over vague
  language when they are supportable.
- Never invent coordinates, dates, or sensor names that are not provided.
"""


TASK_PROMPTS = {
    "describe": """Analyse this remote-sensing image and produce a structured
report. Return JSON with exactly these keys:

{
  "scene_type": "urban|rural|coastal|forest|agricultural|industrial|mixed|other",
  "dominant_land_cover": [  // up to 5 entries, ordered by % coverage
    {"class": "<name>", "approx_percent": <0-100>}
  ],
  "notable_features": [  // salient objects or patterns
    {"feature": "<name>", "count_or_extent": "<string>", "location": "<rough where>"}
  ],
  "likely_resolution_bucket": "sub-meter|1-5m|5-30m|>30m|unknown",
  "image_quality_issues": ["cloud", "haze", "shadow", "motion", "compression", ...],
  "confidence": "high|medium|low",
  "summary": "<2-4 sentence plain-language overview>"
}

Return ONLY the JSON object, no preamble, no markdown fences.""",

    "classify": """Produce a land-cover classification for this image. Return
JSON with exactly these keys:

{
  "scheme": "ESA WorldCover 2021 (10 classes)",
  "classes_present": [
    {
      "class": "<one of: Tree cover, Shrubland, Grassland, Cropland, Built-up,
               Bare/sparse vegetation, Snow and ice, Permanent water bodies,
               Herbaceous wetland, Mangroves, Moss and lichen>",
      "approx_percent": <0-100>,
      "spatial_pattern": "contiguous|fragmented|linear|scattered",
      "evidence": "<what in the image supports this>"
    }
  ],
  "dominant_class": "<class name>",
  "heterogeneity": "low|medium|high",
  "confidence": "high|medium|low",
  "notes": "<anything unusual or ambiguous>"
}

Percentages should sum to ~100. Return ONLY the JSON object.""",

    "detect": """Detect and localise instances of the target categories in
this image. Return JSON with exactly these keys:

{
  "targets_requested": [<list of target strings>],
  "detections": [
    {
      "class": "<target name>",
      "count": <integer>,
      "spatial_distribution": "clustered|dispersed|linear|gridded|edge",
      "approx_regions": ["top-left", "center", "along south edge", ...],
      "size_estimate": "<relative size: small/medium/large, or approx pixels>",
      "confidence": "high|medium|low"
    }
  ],
  "not_detected": [<target names with no visible instances>],
  "caveats": "<resolution / occlusion / ambiguity notes>"
}

Return ONLY the JSON object.""",

    "change": """These two images are of the same (or overlapping) area at
different times. Identify changes between IMAGE 1 (earlier/reference) and
IMAGE 2 (later/target). Return JSON with exactly these keys:

{
  "alignment_quality": "well-aligned|partial-overlap|misaligned|different-area",
  "changes": [
    {
      "change_type": "new_construction|demolition|deforestation|afforestation|
                      flooding|drying|cropland_expansion|cropland_abandonment|
                      road_added|road_removed|other",
      "description": "<what changed>",
      "location": "<rough where in the frame>",
      "magnitude": "small|medium|large",
      "confidence": "high|medium|low"
    }
  ],
  "stable_features": ["<things unchanged>"],
  "overall_verdict": "<1-3 sentence summary of the most significant change>"
}

If alignment_quality is "different-area", set changes to [] and explain in
overall_verdict. Return ONLY the JSON object.""",
}


def build_contents(
    task: str,
    images: list[LoadedImage],
    extra_prompt: str | None = None,
    targets: list[str] | None = None,
) -> list:
    """Assemble multimodal contents for Gemini."""
    parts: list = []

    if task == "change":
        assert len(images) == 2, "change task requires exactly 2 images"
        parts.append("IMAGE 1 (earlier / reference):")
        parts.append(types.Part.from_bytes(data=images[0].data, mime_type=images[0].mime_type))
        parts.append("IMAGE 2 (later / target):")
        parts.append(types.Part.from_bytes(data=images[1].data, mime_type=images[1].mime_type))
    else:
        for img in images:
            parts.append(types.Part.from_bytes(data=img.data, mime_type=img.mime_type))

    # Include geo metadata when present — helps the model ground its output.
    for i, img in enumerate(images, start=1):
        if img.geo_meta:
            parts.append(
                f"[Metadata for image {i}] GeoTIFF: CRS={img.geo_meta.get('crs')}, "
                f"size={img.geo_meta.get('width')}x{img.geo_meta.get('height')}, "
                f"bands={img.geo_meta.get('count')}, dtype={img.geo_meta.get('dtype')}, "
                f"bounds={img.geo_meta.get('bounds')}, res={img.geo_meta.get('resolution')}"
            )

    if task == "custom":
        parts.append(extra_prompt or "Please analyse this remote-sensing image.")
    else:
        prompt = TASK_PROMPTS[task]
        if task == "detect":
            target_str = ", ".join(targets) if targets else "buildings, roads, vehicles"
            prompt = prompt.replace("<list of target strings>", f"[{target_str}]")
            prompt = f"Target categories to detect: {target_str}\n\n{prompt}"
        parts.append(prompt)

    return parts


def call_gemini(
    client: genai.Client,
    model: str,
    contents: list,
    want_json: bool,
) -> str:
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.2,
        response_mime_type="application/json" if want_json else "text/plain",
    )
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    return response.text or ""


def emit_output(
    task: str,
    images: list[LoadedImage],
    raw_text: str,
    want_json: bool,
    out_path: Path | None,
) -> None:
    record = {
        "task": task,
        "model": DEFAULT_MODEL,
        "images": [
            {
                "path": str(img.path),
                "original_size": img.original_size,
                "sent_size": img.sent_size,
                "geo_meta": img.geo_meta,
            }
            for img in images
        ],
    }
    if want_json:
        try:
            record["analysis"] = json.loads(raw_text)
        except json.JSONDecodeError:
            record["analysis_raw"] = raw_text
            record["warning"] = "Model did not return valid JSON."
    else:
        record["analysis_text"] = raw_text

    text = json.dumps(record, indent=2, ensure_ascii=False)
    if out_path:
        out_path.write_text(text, encoding="utf-8")
        print(f"Wrote {out_path}")
    else:
        print(text)


def load_env_file() -> None:
    """Minimal .env loader (no python-dotenv dependency)."""
    candidates = [Path(".env"), Path(__file__).parent / ".env"]
    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def get_client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.stderr.write(
            "ERROR: GEMINI_API_KEY not set. Put it in agent/.env or export it.\n"
            "Get a key at https://aistudio.google.com/apikey\n"
        )
        sys.exit(2)
    return genai.Client(api_key=key)


def iter_images(path: Path, glob: str) -> Iterable[Path]:
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from sorted(path.glob(glob))
    else:
        raise FileNotFoundError(path)


def main() -> None:
    load_env_file()

    parser = argparse.ArgumentParser(
        description="Local remote-sensing analysis with Gemini.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Gemini model id (default: {DEFAULT_MODEL})")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Write result to file instead of stdout.")
    parser.add_argument("--text", action="store_true",
                        help="Request plain text output instead of JSON.")

    sub = parser.add_subparsers(dest="task", required=True)

    p_desc = sub.add_parser("describe", help="General structured description.")
    p_desc.add_argument("image", type=Path)

    p_cls = sub.add_parser("classify", help="Land-cover classification (ESA WorldCover).")
    p_cls.add_argument("image", type=Path)

    p_det = sub.add_parser("detect", help="Detect named object categories.")
    p_det.add_argument("image", type=Path)
    p_det.add_argument("--target", default="buildings,roads,vehicles",
                       help="Comma-separated target categories.")

    p_chg = sub.add_parser("change", help="Change detection between two images.")
    p_chg.add_argument("before", type=Path, help="Earlier image.")
    p_chg.add_argument("after", type=Path, help="Later image.")

    p_cust = sub.add_parser("custom", help="Custom free-form prompt on one image.")
    p_cust.add_argument("image", type=Path)
    p_cust.add_argument("--prompt", required=True)

    p_batch = sub.add_parser("batch", help="Run a task over every image in a directory.")
    p_batch.add_argument("directory", type=Path)
    p_batch.add_argument("--task", required=True,
                         choices=["describe", "classify", "detect"])
    p_batch.add_argument("--glob", default="*.tif",
                         help="Glob pattern (default: *.tif)")
    p_batch.add_argument("--target", default="buildings,roads,vehicles")

    args = parser.parse_args()
    client = get_client()
    want_json = not args.text

    if args.task == "change":
        images = [load_image(args.before), load_image(args.after)]
        contents = build_contents("change", images)
        text = call_gemini(client, args.model, contents, want_json)
        emit_output("change", images, text, want_json, args.output)
        return

    if args.task == "custom":
        images = [load_image(args.image)]
        contents = build_contents("custom", images, extra_prompt=args.prompt)
        text = call_gemini(client, args.model, contents, want_json=False)
        emit_output("custom", images, text, want_json=False, out_path=args.output)
        return

    if args.task == "batch":
        paths = list(iter_images(args.directory, args.glob))
        if not paths:
            sys.stderr.write(f"No images matched {args.glob} under {args.directory}\n")
            sys.exit(1)
        all_records = []
        targets = [t.strip() for t in args.target.split(",")] if args.target else None
        for i, p in enumerate(paths, 1):
            print(f"[{i}/{len(paths)}] {p}", file=sys.stderr)
            img = load_image(p)
            contents = build_contents(args.task, [img], targets=targets)
            text = call_gemini(client, args.model, contents, want_json=True)
            try:
                analysis = json.loads(text)
            except json.JSONDecodeError:
                analysis = {"_raw": text, "_warning": "non-JSON response"}
            all_records.append({
                "path": str(p),
                "original_size": img.original_size,
                "geo_meta": img.geo_meta,
                "analysis": analysis,
            })
        payload = json.dumps(
            {"task": args.task, "model": args.model, "results": all_records},
            indent=2, ensure_ascii=False,
        )
        if args.output:
            args.output.write_text(payload, encoding="utf-8")
            print(f"Wrote {args.output}", file=sys.stderr)
        else:
            print(payload)
        return

    # Single-image tasks: describe / classify / detect
    images = [load_image(args.image)]
    targets = None
    if args.task == "detect":
        targets = [t.strip() for t in args.target.split(",")] if args.target else None
    contents = build_contents(args.task, images, targets=targets)
    text = call_gemini(client, args.model, contents, want_json)
    emit_output(args.task, images, text, want_json, args.output)


if __name__ == "__main__":
    main()
