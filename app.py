import json
import os
import random
import sqlite3
import uuid
from datetime import datetime
from shutil import copy2

from flask import Flask, redirect, render_template, request, session, url_for
from PIL import Image, ImageFilter, ImageOps, ImageStat

import inspiration_tags

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_ROOT, "smiles.db")
GENERATED_DIR = os.path.join(APP_ROOT, "static", "generated")
INSPIRATION_DIR = os.path.join(APP_ROOT, "static", "inspiration_images")

app = Flask(__name__)
app.secret_key = os.environ.get("SMILE_SECRET", "smile-secret-key")


CATEGORIES = {
    "actors": inspiration_tags.ACTORS,
    "activities": inspiration_tags.ACTIVITIES,
    "areas": inspiration_tags.AREAS,
    "accessories": inspiration_tags.ACCESSORIES,
}


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


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


def weighted_choice(category: str, options: list[str]) -> str:
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


def generate_inspiration() -> dict[str, str]:
    return {
        "actors": weighted_choice("actors", inspiration_tags.ACTORS),
        "activities": weighted_choice("activities", inspiration_tags.ACTIVITIES),
        "areas": weighted_choice("areas", inspiration_tags.AREAS),
        "accessories": weighted_choice("accessories", inspiration_tags.ACCESSORIES),
    }


def list_inspiration_images() -> list[str]:
    if not os.path.isdir(INSPIRATION_DIR):
        return []
    supported = (".png", ".jpg", ".jpeg", ".webp")
    return [
        os.path.join(INSPIRATION_DIR, filename)
        for filename in os.listdir(INSPIRATION_DIR)
        if filename.lower().endswith(supported)
    ]


def choose_inspiration_background(width: int, height: int) -> tuple[Image.Image | None, tuple[int, int, int]]:
    images = list_inspiration_images()
    if not images:
        return None, (110, 110, 110)

    source_path = random.choice(images)
    with Image.open(source_path) as source:
        source = source.convert("RGB")
        background = ImageOps.fit(source, (width, height), method=Image.LANCZOS)

    blurred = background.filter(ImageFilter.GaussianBlur(radius=3))
    stats = ImageStat.Stat(blurred)
    accent = tuple(int(channel) for channel in stats.mean[:3])
    return blurred, accent


def generate_image(selections: dict[str, str]) -> str:
    width, height = 900, 520
    inspiration_background, _ = choose_inspiration_background(width, height)
    if inspiration_background is None:
        background = (
            random.randint(80, 200),
            random.randint(80, 200),
            random.randint(80, 200),
        )
        image = Image.new("RGB", (width, height), color=background)
    else:
        image = inspiration_background

    image = image.convert("RGBA")

    unique_id = uuid.uuid4().hex
    filename = f"smile_{unique_id}.png"
    filepath = os.path.join(GENERATED_DIR, filename)
    image.convert("RGB").save(filepath, format="PNG")
    return f"generated/{filename}"


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
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    selections = generate_inspiration()
    image_path = generate_image(selections)
    session["last_selection"] = selections
    session["last_image"] = image_path
    return render_template("image.html", image_path=image_path, selections=selections)


@app.route("/rate", methods=["POST"])
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
        for category, item in selections.items():
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
