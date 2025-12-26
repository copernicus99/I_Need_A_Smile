import base64
import json
import os
import random
import sqlite3
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from io import BytesIO
from shutil import copy2

from flask import Flask, redirect, render_template, request, session, url_for
from PIL import Image, ImageOps

import inspiration_tags
import settings

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_ROOT, "smiles.db")
GENERATED_DIR = os.path.join(APP_ROOT, "static", "generated")
INSPIRATION_DIR = os.path.join(APP_ROOT, "static", "inspiration_images")

app = Flask(__name__)
app.secret_key = os.environ.get("SMILE_SECRET", "smile-secret-key")


CATEGORIES = {
    "actor_protagonist": inspiration_tags.ACTOR_PROTGONIST,
    "actor_supporting": inspiration_tags.ACTOR_SUPPORTING,
    "activities": inspiration_tags.ACTIVITIES,
    "areas": inspiration_tags.AREAS,
    "accessories": inspiration_tags.ACCESSORIES,
    "art_style": inspiration_tags.ART_STYLE,
}


# Open a database connection with row access by column name.
def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


# Ensure storage and metadata tables are ready for the app lifecycle.
def init_db() -> None:
    os.makedirs(GENERATED_DIR, exist_ok=True)
    os.makedirs(INSPIRATION_DIR, exist_ok=True)
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                total_score INTEGER NOT NULL DEFAULT 0,
                rating_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (category, name)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rating INTEGER NOT NULL,
                selections TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        for category, options in CATEGORIES.items():
            for option in options:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO items (category, name, total_score, rating_count)
                    VALUES (?, ?, 0, 0)
                    """,
                    (category, option),
                )


# Use ratings as weights unless recognition is disabled in settings.
def weighted_choice(category: str, options: list[str]) -> str:
    if not settings.RECOGNIZE_RATINGS:
        return random.choice(options)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT name, total_score, rating_count FROM items WHERE category = ?",
            (category,),
        ).fetchall()
    weights = []
    names = []
    row_map = {row["name"]: row for row in rows}
    for option in options:
        row = row_map.get(option)
        if row and row["rating_count"] > 0:
            average = row["total_score"] / row["rating_count"]
        else:
            average = 0
        weight = 1 + average
        names.append(option)
        weights.append(weight)
    return random.choices(names, weights=weights, k=1)[0]


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


# Pick a themed set of inspiration tags for the prompt.
def generate_inspiration() -> dict[str, list[str]]:
    return {
        "actor_protagonist": weighted_choices(
            "actor_protagonist",
            inspiration_tags.ACTOR_PROTGONIST,
            pick_count(inspiration_tags.ACTOR_PROTGONIST),
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
            pick_count(inspiration_tags.AREAS),
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


def build_prompt(selections: dict[str, list[str]]) -> str:
    protagonist = format_tag_list(selections["actor_protagonist"])
    supporting = format_tag_list(selections["actor_supporting"])
    activities = format_tag_list(selections["activities"])
    areas = format_tag_list(selections["areas"])
    accessories = format_tag_list(selections["accessories"])
    art_style = format_tag_list(selections["art_style"])
    return (
        "Create a whimsical, joyful illustration intended to elicit laughter from viewer. "
        f"Scene: {protagonist} with {supporting} {activities} {areas} with {accessories}. "
        "Include dynamic action and strong character expressions. "
        "Ensure the scene clearly shows the actors (main and supporting), activity, area, accessory "
        "and is rendered in the specified style. "
        f"Render in a {art_style} style with a warm, whimsical palette."
    )


# Keep highly rated images as inspiration inputs for later generations.
def save_inspiration_image(image_path: str) -> None:
    if not image_path:
        return
    source_path = os.path.join(APP_ROOT, "static", image_path)
    if not os.path.exists(source_path):
        return
    inspiration_name = f"inspiration_{uuid.uuid4().hex}.png"
    destination_path = os.path.join(INSPIRATION_DIR, inspiration_name)
    copy2(source_path, destination_path)


init_db()


@app.route("/")
# Render the landing page with the primary call-to-action.
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
# Generate a fresh set of picks and request the corresponding image.
def generate():
    selections = generate_inspiration()
    try:
        image_path = generate_image(selections)
    except RuntimeError as exc:
        return render_template("image.html", error=str(exc), selections=selections)
    session["last_selection"] = selections
    session["last_image"] = image_path
    return render_template("image.html", image_path=image_path, selections=selections)


@app.route("/rate", methods=["POST"])
# Persist the rating and update aggregate scores for weighted choices.
def rate():
    rating_value = int(request.form.get("rating", "0"))
    selections = session.get("last_selection")
    image_path = session.get("last_image")
    if not selections or rating_value not in range(1, 6):
        return redirect(url_for("index"))

    created_at = datetime.utcnow().isoformat()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO ratings (rating, selections, created_at) VALUES (?, ?, ?)",
            (rating_value, json.dumps(selections), created_at),
        )
        for category, items in selections.items():
            for item in items:
                connection.execute(
                    """
                    UPDATE items
                    SET total_score = total_score + ?,
                        rating_count = rating_count + 1
                    WHERE category = ? AND name = ?
                    """,
                    (rating_value, category, item),
                )

    if rating_value == 5:
        save_inspiration_image(image_path)

    session.pop("last_selection", None)
    session.pop("last_image", None)
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
