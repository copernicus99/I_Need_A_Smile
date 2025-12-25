import json
import os
import random
import sqlite3
import textwrap
import uuid
from datetime import datetime
from shutil import copy2

from flask import Flask, redirect, render_template, request, session, url_for
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

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


def extract_palette(source: Image.Image, count: int = 6) -> list[tuple[int, int, int]]:
    compact = source.resize((64, 64))
    pixels = list(compact.getdata())
    if not pixels:
        return []
    sample = random.sample(pixels, k=min(count, len(pixels)))
    return [tuple(int(channel) for channel in color[:3]) for color in sample]


def choose_inspiration_palette() -> list[tuple[int, int, int]]:
    images = list_inspiration_images()
    if not images:
        return [
            (
                random.randint(80, 200),
                random.randint(80, 200),
                random.randint(80, 200),
            )
            for _ in range(5)
        ]

    source_path = random.choice(images)
    with Image.open(source_path) as source:
        source = source.convert("RGB")
        palette = extract_palette(source)

    if not palette:
        palette = [(120, 120, 120)]
    return palette


def create_gradient_background(width: int, height: int, palette: list[tuple[int, int, int]]) -> Image.Image:
    start, end = random.sample(palette, k=min(2, len(palette)))
    image = Image.new("RGB", (width, height), start)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(
            int(start[channel] * (1 - ratio) + end[channel] * ratio) for channel in range(3)
        )
        draw.line([(0, y), (width, y)], fill=color)
    return image


def add_whimsical_layers(image: Image.Image, palette: list[tuple[int, int, int]]) -> Image.Image:
    width, height = image.size
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(18):
        color = random.choice(palette)
        alpha = random.randint(60, 140)
        size = random.randint(int(width * 0.12), int(width * 0.4))
        x = random.randint(-size // 3, width - size // 2)
        y = random.randint(-size // 3, height - size // 2)
        draw.ellipse([x, y, x + size, y + size], fill=(*color, alpha))
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=14))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)

    draw = ImageDraw.Draw(image)
    for _ in range(9):
        color = random.choice(palette)
        alpha = random.randint(120, 210)
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        stroke = random.randint(4, 12)
        draw.line((x1, y1, x2, y2), fill=(*color, alpha), width=stroke)
    return image


def add_story_elements(image: Image.Image, selections: dict[str, str], palette: list[tuple[int, int, int]]) -> None:
    width, height = image.size
    draw = ImageDraw.Draw(image)

    for _ in range(2):
        radius = random.randint(35, 70)
        cx = random.randint(int(width * 0.2), int(width * 0.8))
        cy = random.randint(int(height * 0.3), int(height * 0.7))
        face_color = random.choice(palette)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(*face_color, 210))
        eye_offset = radius // 3
        for side in (-1, 1):
            ex = cx + side * eye_offset
            ey = cy - radius // 5
            draw.ellipse((ex - 6, ey - 6, ex + 6, ey + 6), fill=(20, 20, 20, 220))
        draw.arc(
            (cx - radius // 2, cy, cx + radius // 2, cy + radius // 2),
            start=0,
            end=180,
            fill=(30, 30, 30, 220),
            width=3,
        )

    caption = (
        f"{selections['actors']} {selections['activities']} in {selections['areas']} "
        f"with {selections['accessories']}"
    )
    lines = textwrap.wrap(caption, width=42)
    font = ImageFont.load_default()
    line_sizes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    max_width = max(size[2] - size[0] for size in line_sizes)
    total_height = sum(size[3] - size[1] for size in line_sizes) + (len(lines) - 1) * 4
    x = (width - max_width) // 2
    y = height - total_height - 36
    padding = 12
    banner_color = random.choice(palette)
    draw.rectangle(
        (x - padding, y - padding, x + max_width + padding, y + total_height + padding),
        fill=(*banner_color, 170),
    )
    current_y = y
    for line, size in zip(lines, line_sizes):
        draw.text((x, current_y), line, fill=(20, 20, 20, 230), font=font)
        current_y += size[3] - size[1] + 4


def generate_image(selections: dict[str, str]) -> str:
    width, height = 900, 520
    palette = choose_inspiration_palette()
    image = create_gradient_background(width, height, palette)
    image = add_whimsical_layers(image, palette)
    image = image.convert("RGBA")
    add_story_elements(image, selections, palette)

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
