import base64
import json
import os
import random
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from io import BytesIO
from shutil import copy2

from flask import Flask, redirect, render_template, request, session, url_for
from PIL import Image, ImageOps

import inspiration_tags
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
PROMPT_LOG_PATH = os.path.join(APP_ROOT, "prompt_log.txt")
GENERATED_DIR = os.path.join(APP_ROOT, "static", "generated")
ALBUM_DIR = os.path.join(APP_ROOT, "static", "album_images")

app = Flask(__name__)
app.secret_key = os.environ.get("SMILE_SECRET", "smile-secret-key")


CATEGORIES = {
    "actor_protagonist": inspiration_tags.ACTOR_PROTGONIST,
    "actor_supporting": inspiration_tags.ACTOR_SUPPORTING,
    "activities": inspiration_tags.ACTIVITIES,
    "areas": inspiration_tags.AREAS,
    "accessories": inspiration_tags.ACCESSORIES,
    "art_style": inspiration_tags.ART_STYLE,
    "villan": inspiration_tags.VILLAN,
}


# Ensure storage directories are ready for the app lifecycle.
def init_storage() -> None:
    os.makedirs(GENERATED_DIR, exist_ok=True)
    os.makedirs(ALBUM_DIR, exist_ok=True)


def weighted_choice(category: str, options: list[str]) -> str:
    return random.choice(options)


def weighted_choices(category: str, options: list[str], count: int) -> list[str]:
    selections = []
    remaining = list(options)
    for _ in range(min(count, len(remaining))):
        pick = weighted_choice(category, remaining)
        selections.append(pick)
        remaining.remove(pick)
    return selections


def pick_count(options: list[str], max_count: int = 2) -> int:
    if len(options) < 2:
        return 1
    return random.randint(1, min(max_count, len(options)))


def load_prompt_entries() -> list[str]:
    if not os.path.exists(PROMPT_LOG_PATH):
        return []
    with open(PROMPT_LOG_PATH, "r", encoding="utf-8") as log_file:
        content = log_file.read()
    entries = [entry.strip() for entry in content.split("\n\n") if entry.strip()]
    return entries


def load_recent_prompt_entries(limit: int) -> list[str]:
    entries = load_prompt_entries()
    return entries[-limit:]


def recent_entries_contain_any(entries: list[str], tags: list[str]) -> bool:
    return any(tag in entry for entry in entries for tag in tags)


def count_entries_containing_any(entries: list[str], tags: list[str]) -> int:
    return sum(1 for entry in entries if any(tag in entry for tag in tags))


# Pick a themed set of inspiration tags for the prompt.
def generate_inspiration() -> dict[str, list[str]]:
    all_entries = load_prompt_entries()
    total_entries = len(all_entries)
    actor_protagonist_entries = count_entries_containing_any(
        all_entries, inspiration_tags.ACTOR_PROTGONIST
    )
    include_actor_protagonist = (total_entries + 1) % 4 == 0
    include_villan = include_actor_protagonist and (actor_protagonist_entries + 1) % 3 == 0
    return {
        "actor_protagonist": (
            weighted_choices(
                "actor_protagonist",
                inspiration_tags.ACTOR_PROTGONIST,
                1,
            )
            if include_actor_protagonist
            else []
        ),
        "actor_supporting": weighted_choices(
            "actor_supporting",
            inspiration_tags.ACTOR_SUPPORTING,
            pick_count(inspiration_tags.ACTOR_SUPPORTING),
        ),
        "activities": weighted_choices(
            "activities",
            inspiration_tags.ACTIVITIES,
            pick_count(inspiration_tags.ACTIVITIES),
        ),
        "areas": weighted_choices(
            "areas",
            inspiration_tags.AREAS,
            1,
        ),
        "accessories": weighted_choices(
            "accessories",
            inspiration_tags.ACCESSORIES,
            pick_count(inspiration_tags.ACCESSORIES),
        ),
        "art_style": weighted_choices(
            "art_style",
            inspiration_tags.ART_STYLE,
            1,
        ),
        "villan": (
            weighted_choices(
                "villan",
                inspiration_tags.VILLAN,
                1,
            )
            if include_villan
            else []
        ),
    }


# Build the prompt and rely on the external image API for rendering.
def generate_image(selections: dict[str, list[str]]) -> str:
    width, height = 900, 520
    image = generate_ai_image(selections, width, height)

    unique_id = uuid.uuid4().hex
    filename = f"smile_{unique_id}.png"
    filepath = os.path.join(GENERATED_DIR, filename)
    image.convert("RGB").save(filepath, format="PNG")
    return f"generated/{filename}"


# Send the final prompt to the OpenAI image generation API.
def generate_ai_image(selections: dict[str, list[str]], width: int, height: int) -> Image.Image:
    api_key = os.environ.get("SMILE_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing SMILE_IMAGE_API_KEY or OPENAI_API_KEY for AI image generation.")

    model = os.environ.get("SMILE_IMAGE_MODEL", "gpt-image-1")
    prompt = build_prompt(selections)
    log_prompt(selections)
    payload = {
        "model": model,
        "prompt": prompt,
        "size": os.environ.get("SMILE_IMAGE_SIZE", "1024x1024"),
    }
    request_data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("SMILE_IMAGE_API_URL", "https://api.openai.com/v1/images/generations"),
        data=request_data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        raise RuntimeError(f"Image generation failed: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Image generation failed: {exc.reason}") from exc

    data = json.loads(response_body.decode("utf-8"))
    image_data = data.get("data", [])
    if not image_data:
        raise RuntimeError("Image generation failed: no image data returned.")

    encoded = image_data[0].get("b64_json")
    if not encoded:
        raise RuntimeError("Image generation failed: missing image payload.")

    raw_bytes = base64.b64decode(encoded)
    with Image.open(BytesIO(raw_bytes)) as generated:
        generated = generated.convert("RGB")
        return ImageOps.fit(generated, (width, height), method=Image.LANCZOS)


# Compose a consistent, detailed prompt for image generation.
def format_tag_list(tags: list[str]) -> str:
    if not tags:
        return ""
    if len(tags) == 1:
        return tags[0]
    if len(tags) == 2:
        return f"{tags[0]} and {tags[1]}"
    return f"{', '.join(tags[:-1])}, and {tags[-1]}"


def build_scene_description(selections: dict[str, list[str]]) -> str:
    protagonist = format_tag_list(selections.get("actor_protagonist", []))
    supporting = format_tag_list(selections.get("actor_supporting", []))
    activities = format_tag_list(selections.get("activities", []))
    areas = format_tag_list(selections.get("areas", []))
    accessories = format_tag_list(selections.get("accessories", []))
    villan = format_tag_list(selections.get("villan", []))
    actor_bits = [bit for bit in (protagonist, villan) if bit]
    parts = []
    if actor_bits:
        parts.append(" and ".join(actor_bits))
    if supporting:
        if actor_bits:
            parts.append(f"with {supporting}")
        else:
            parts.append(supporting)
    if activities:
        parts.append(activities)
    if areas:
        parts.append(areas)
    if accessories:
        parts.append(f"with {accessories}")
    return f"{' '.join(parts).strip()}."


def build_prompt(selections: dict[str, list[str]]) -> str:
    art_style = format_tag_list(selections["art_style"])
    scene_description = build_scene_description(selections)
    return (
        "Create a whimsical, joyful illustration intended to elicit laughter from viewer. "
        f"Scene: {scene_description} "
        "Include dynamic action and strong character expressions. "
        "Ensure the scene clearly shows the actors (main when present and supporting), activity, area, accessory "
        "and is rendered in the specified style. "
        f"Render in a {art_style} style."
    )


def log_prompt(selections: dict[str, list[str]]) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    art_style = format_tag_list(selections["art_style"])
    scene_description = build_scene_description(selections)
    entry = (
        f"{timestamp} |\n"
        f"Scene: {scene_description}\n"
        f"Render in a {art_style} style\n\n"
    )
    with open(PROMPT_LOG_PATH, "a", encoding="utf-8") as log_file:
        log_file.write(entry)


# Keep saved images as album inputs for later generations.
def save_album_image(image_path: str) -> None:
    if not image_path:
        return
    source_path = os.path.join(APP_ROOT, "static", image_path)
    if not os.path.exists(source_path):
        return
    album_name = f"album_{uuid.uuid4().hex}.png"
    destination_path = os.path.join(ALBUM_DIR, album_name)
    copy2(source_path, destination_path)


def list_album_images(limit: int = 12) -> list[str]:
    if not os.path.isdir(ALBUM_DIR):
        return []
    files = [
        filename
        for filename in os.listdir(ALBUM_DIR)
        if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    ]
    files.sort(
        key=lambda filename: os.path.getmtime(os.path.join(ALBUM_DIR, filename)),
        reverse=True,
    )
    return [f"album_images/{filename}" for filename in files[:limit]]


init_storage()


@app.route("/")
# Render the landing page with the primary call-to-action.
def index():
    return render_template("index.html")


@app.route("/wait")
# Render a waiting experience while the image is generated.
def wait():
    album_images = list_album_images()
    return render_template("wait.html", album_images=album_images)


@app.route("/generate_async", methods=["POST"])
# Generate a fresh set of picks and request the corresponding image.
def generate_async():
    selections = generate_inspiration()
    try:
        image_path = generate_image(selections)
    except RuntimeError as exc:
        session["last_error"] = str(exc)
        session.pop("last_image", None)
        session["last_selection"] = selections
        return {"status": "error", "message": str(exc)}
    session["last_selection"] = selections
    session["last_image"] = image_path
    session.pop("last_error", None)
    return {"status": "ok"}


@app.route("/image")
def image():
    error = session.get("last_error")
    image_path = session.get("last_image")
    selections = session.get("last_selection")
    if not error and not image_path:
        return redirect(url_for("index"))
    return render_template(
        "image.html",
        error=error,
        image_path=image_path,
        selections=selections,
    )


@app.route("/album", methods=["POST"])
# Save the current image into the album.
def album():
    image_path = session.get("last_image")
    if not image_path:
        return redirect(url_for("index"))
    save_album_image(image_path)
    session.pop("last_image", None)
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_storage()
    app.run(host="0.0.0.0", port=5000, debug=True)
