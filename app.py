import base64
import json
import os
import random
import sqlite3
import textwrap
import uuid
import urllib.error
import urllib.request
from io import BytesIO
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

    def adjust_color(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
        return tuple(max(0, min(255, int(channel * factor))) for channel in color)

    def draw_owl(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        radius = min(x2 - x1, y2 - y1) // 2
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)
        eye_radius = radius // 3
        for offset in (-eye_radius, eye_radius):
            draw.ellipse(
                (cx + offset - eye_radius, cy - eye_radius, cx + offset + eye_radius, cy + eye_radius),
                fill=(245, 245, 245),
            )
            draw.ellipse(
                (
                    cx + offset - eye_radius // 3,
                    cy - eye_radius // 3,
                    cx + offset + eye_radius // 3,
                    cy + eye_radius // 3,
                ),
                fill=(40, 40, 40),
            )
        draw.polygon(
            [(cx, cy + eye_radius // 2), (cx - eye_radius // 2, cy + eye_radius), (cx + eye_radius // 2, cy + eye_radius)],
            fill=(245, 180, 60),
        )

    def draw_cat(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        radius = min(x2 - x1, y2 - y1) // 2
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)
        ear_size = radius // 2
        draw.polygon([(cx - radius, cy - radius // 2), (cx - radius + ear_size, cy - radius - ear_size), (cx, cy - radius // 2)], fill=fill)
        draw.polygon([(cx + radius, cy - radius // 2), (cx + radius - ear_size, cy - radius - ear_size), (cx, cy - radius // 2)], fill=fill)
        draw.ellipse((cx - radius // 2, cy - radius // 4, cx - radius // 3, cy), fill=(40, 40, 40))
        draw.ellipse((cx + radius // 3, cy - radius // 4, cx + radius // 2, cy), fill=(40, 40, 40))
        draw.line((cx - radius // 2, cy + radius // 6, cx - radius, cy + radius // 4), fill=(40, 40, 40), width=2)
        draw.line((cx + radius // 2, cy + radius // 6, cx + radius, cy + radius // 4), fill=(40, 40, 40), width=2)

    def draw_chicken(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        radius = min(x2 - x1, y2 - y1) // 2
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)
        comb_radius = radius // 3
        draw.ellipse((cx - comb_radius, cy - radius - comb_radius // 2, cx + comb_radius, cy - radius + comb_radius), fill=(220, 60, 60))
        draw.polygon(
            [(cx + radius // 2, cy, cx + radius, cy + radius // 6, cx + radius // 2, cy + radius // 3)],
            fill=(245, 180, 60),
        )
        draw.ellipse((cx - radius // 3, cy - radius // 4, cx - radius // 6, cy), fill=(30, 30, 30))

    def draw_skunk(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.ellipse(box, fill=fill)
        cx = (x1 + x2) // 2
        draw.rectangle((cx - 6, y1 + 6, cx + 6, y2 - 6), fill=(245, 245, 245))
        draw.ellipse((x2 - 18, y1 + 8, x2, y1 + 26), fill=(245, 245, 245))

    def draw_possum(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.ellipse(box, fill=fill)
        draw.ellipse((x1 + 8, y1 + 12, x1 + 24, y1 + 28), fill=(245, 245, 245))
        draw.ellipse((x2 - 24, y1 + 12, x2 - 8, y1 + 28), fill=(245, 245, 245))
        draw.line((x2 - 6, y2 - 12, x2 + 18, y2 - 2), fill=(150, 150, 150), width=4)

    def draw_squirrel(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.ellipse((x1 + 10, y1 + 10, x2 - 20, y2 - 10), fill=fill)
        draw.ellipse((x2 - 30, y1 - 6, x2 + 10, y2 - 20), outline=fill, width=6)

    def draw_vaping(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 10, y2 - 20, x1 + 38, y2 - 8), fill=fill)
        draw.line((x1 + 38, y2 - 14, x1 + 58, y2 - 20), fill=fill, width=4)
        for offset in range(3):
            draw.arc((x1 + 40 + offset * 8, y1 + 6, x1 + 58 + offset * 8, y1 + 24), 200, 20, fill=(235, 235, 235), width=3)

    def draw_sleeping(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.ellipse((x1 + 10, y1 + 10, x2 - 10, y2 - 10), outline=fill, width=4)
        draw.text((x1 + 18, y1 + 8), "Zz", fill=fill, font=ImageFont.load_default())

    def draw_falling(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.line((x1 + 20, y1 + 20, x2 - 20, y2 - 20), fill=fill, width=4)
        draw.ellipse((x2 - 26, y2 - 26, x2 - 10, y2 - 10), fill=fill)

    def draw_slipping(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.arc((x1 + 8, y2 - 30, x1 + 46, y2 - 6), 200, 20, fill=(245, 215, 90), width=6)
        draw.line((x1 + 50, y1 + 20, x2 - 20, y2 - 10), fill=fill, width=4)
        draw.ellipse((x2 - 20, y2 - 20, x2 - 6, y2 - 6), fill=fill)

    def draw_laughing(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.ellipse(box, fill=fill)
        draw.arc((x1 + 14, y1 + 14, x2 - 14, y2 - 14), 200, 340, fill=(30, 30, 30), width=4)
        draw.ellipse((x1 + 20, y1 + 20, x1 + 32, y1 + 32), fill=(30, 30, 30))
        draw.ellipse((x2 - 32, y1 + 20, x2 - 20, y1 + 32), fill=(30, 30, 30))

    def draw_mischief(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle(box, outline=fill, width=4)
        draw.ellipse((x1 + 12, y1 + 18, x1 + 30, y1 + 36), fill=fill)
        draw.ellipse((x2 - 30, y1 + 18, x2 - 12, y1 + 36), fill=fill)
        draw.arc((x1 + 16, y2 - 30, x2 - 16, y2 - 10), 0, 180, fill=fill, width=3)

    def draw_snowboarding(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.line((x1 + 10, y2 - 18, x2 - 10, y2 - 6), fill=fill, width=6)
        draw.line((x1 + 20, y1 + 20, x2 - 30, y2 - 24), fill=fill, width=4)
        draw.ellipse((x2 - 34, y2 - 42, x2 - 18, y2 - 26), fill=fill)

    def draw_fishing(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.line((x1 + 12, y1 + 10, x2 - 12, y2 - 10), fill=fill, width=4)
        draw.line((x2 - 12, y2 - 10, x2 - 6, y2 + 4), fill=fill, width=2)
        draw.ellipse((x2 - 10, y2, x2 + 4, y2 + 14), outline=fill, width=3)

    def draw_car(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 10, y1 + 20, x2 - 10, y2 - 12), fill=fill)
        draw.rectangle((x1 + 24, y1 + 6, x2 - 24, y1 + 24), fill=fill)
        draw.ellipse((x1 + 16, y2 - 18, x1 + 34, y2), fill=(30, 30, 30))
        draw.ellipse((x2 - 34, y2 - 18, x2 - 16, y2), fill=(30, 30, 30))

    def draw_bar(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 10, y2 - 24, x2 - 10, y2 - 8), fill=fill)
        for offset in (0, 18, 36):
            draw.rectangle((x1 + 12 + offset, y1 + 12, x1 + 20 + offset, y2 - 26), fill=(240, 240, 240))

    def draw_beach(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.ellipse((x1 + 8, y1 + 8, x1 + 28, y1 + 28), fill=(245, 215, 90))
        draw.rectangle((x1 + 8, y2 - 18, x2 - 8, y2 - 8), fill=(90, 170, 220))
        draw.arc((x1 + 8, y2 - 30, x2 - 8, y2 - 6), 0, 180, fill=(240, 210, 140), width=4)

    def draw_store_sign(box: tuple[int, int, int, int], label: str, fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 8, y1 + 10, x2 - 8, y2 - 12), outline=fill, width=3)
        draw.text((x1 + 12, y1 + 18), label, fill=fill, font=ImageFont.load_default())

    def draw_concert(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.line((x1 + 20, y1 + 10, x1 + 20, y2 - 10), fill=fill, width=4)
        draw.ellipse((x1 + 20, y1 + 10, x2 - 10, y1 + 34), outline=fill, width=4)
        draw.text((x2 - 24, y2 - 26), "♪", fill=fill, font=ImageFont.load_default())

    def draw_cigarettes(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 12, y2 - 22, x2 - 12, y2 - 14), fill=fill)
        draw.rectangle((x2 - 24, y2 - 22, x2 - 12, y2 - 14), fill=(240, 200, 120))
        draw.arc((x1 + 6, y1 + 6, x1 + 24, y1 + 24), 200, 20, fill=(220, 220, 220), width=2)

    def draw_hat(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 16, y1 + 18, x2 - 16, y2 - 12), fill=fill)
        draw.rectangle((x1 + 8, y2 - 16, x2 - 8, y2 - 8), fill=fill)

    def draw_boot(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 20, y1 + 10, x1 + 36, y2 - 16), fill=fill)
        draw.rectangle((x1 + 20, y2 - 16, x2 - 12, y2 - 8), fill=fill)

    def draw_mushroom(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.arc((x1 + 8, y1 + 4, x2 - 8, y2 - 10), 0, 180, fill=fill, width=6)
        draw.rectangle((x1 + 24, y1 + 24, x2 - 24, y2 - 8), fill=(245, 230, 210))

    def draw_speech_bubble(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 10, y1 + 10, x2 - 10, y2 - 16), outline=fill, width=3)
        draw.polygon([(x1 + 24, y2 - 16), (x1 + 30, y2 - 2), (x1 + 40, y2 - 16)], fill=fill)
        draw.text((x1 + 22, y1 + 18), "!", fill=fill, font=ImageFont.load_default())

    def draw_gun(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle((x1 + 10, y1 + 20, x2 - 12, y1 + 32), fill=fill)
        draw.rectangle((x1 + 28, y1 + 32, x1 + 42, y2 - 10), fill=fill)
        draw.rectangle((x2 - 18, y1 + 22, x2 - 6, y1 + 28), fill=fill)

    def draw_wheel(box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = box
        draw.ellipse(box, outline=fill, width=4)
        draw.line((x1 + 10, (y1 + y2) // 2, x2 - 10, (y1 + y2) // 2), fill=fill, width=2)
        draw.line(((x1 + x2) // 2, y1 + 10, (x1 + x2) // 2, y2 - 10), fill=fill, width=2)

    def draw_icon_for_tag(tag: str, box: tuple[int, int, int, int], fill: tuple[int, int, int]) -> None:
        lower_tag = tag.lower()
        if "owl" in lower_tag:
            draw_owl(box, fill)
        elif "cat" in lower_tag or "kitten" in lower_tag:
            draw_cat(box, fill)
            if "dirty" in lower_tag:
                draw.ellipse((box[0] + 8, box[1] + 12, box[0] + 18, box[1] + 22), fill=(90, 90, 90))
        elif "skunk" in lower_tag:
            draw_skunk(box, fill)
        elif "possum" in lower_tag:
            draw_possum(box, fill)
        elif "chicken" in lower_tag:
            draw_chicken(box, fill)
        elif "squirrel" in lower_tag:
            draw_squirrel(box, fill)
        elif "vaping" in lower_tag:
            draw_vaping(box, fill)
        elif "sleep" in lower_tag:
            draw_sleeping(box, fill)
        elif "falling" in lower_tag:
            draw_falling(box, fill)
        elif "slipping" in lower_tag:
            draw_slipping(box, fill)
        elif "laugh" in lower_tag:
            draw_laughing(box, fill)
        elif "mischief" in lower_tag:
            draw_mischief(box, fill)
        elif "snowboard" in lower_tag:
            draw_snowboarding(box, fill)
        elif "fishing" in lower_tag:
            draw_fishing(box, fill)
        elif "car" in lower_tag:
            draw_car(box, fill)
        elif "bar" in lower_tag:
            draw_bar(box, fill)
        elif "beach" in lower_tag:
            draw_beach(box, fill)
        elif "wawa" in lower_tag:
            draw_store_sign(box, "Wawa", fill)
        elif "rock concert" in lower_tag:
            draw_concert(box, fill)
        elif "applebee" in lower_tag:
            draw_store_sign(box, "Applebee's", fill)
        elif "cigarette" in lower_tag:
            draw_cigarettes(box, fill)
        elif "hat" in lower_tag:
            draw_hat(box, fill)
        elif "boot" in lower_tag:
            draw_boot(box, fill)
        elif "mushroom" in lower_tag:
            draw_mushroom(box, fill)
        elif "bitchy woman" in lower_tag:
            draw_speech_bubble(box, fill)
        elif "gun" in lower_tag:
            draw_gun(box, fill)
        elif "wheel" in lower_tag:
            draw_wheel(box, fill)
        else:
            draw.rectangle(box, outline=fill, width=4)

    font = ImageFont.load_default()
    cards = [
        ("Actors", selections["actors"]),
        ("Activities", selections["activities"]),
        ("Area", selections["areas"]),
        ("Accessory", selections["accessories"]),
    ]
    card_width = int(width * 0.42)
    card_height = int(height * 0.32)
    padding = int(width * 0.04)
    positions = [
        (padding, padding),
        (width - card_width - padding, padding),
        (padding, height - card_height - padding),
        (width - card_width - padding, height - card_height - padding),
    ]

    for (title, value), (x, y) in zip(cards, positions):
        base_color = random.choice(palette)
        card_color = (*adjust_color(base_color, 1.1), 220)
        outline_color = adjust_color(base_color, 0.7)
        draw.rounded_rectangle(
            (x, y, x + card_width, y + card_height),
            radius=24,
            fill=card_color,
            outline=outline_color,
            width=3,
        )
        draw.text((x + 18, y + 14), title, fill=(30, 30, 30, 230), font=font)
        value_lines = textwrap.wrap(value, width=18)
        text_y = y + 36
        for line in value_lines:
            draw.text((x + 18, text_y), line, fill=(25, 25, 25, 240), font=font)
            text_y += 14
        icon_box = (
            x + card_width - 86,
            y + 24,
            x + card_width - 18,
            y + 92,
        )
        draw_icon_for_tag(value, icon_box, outline_color)


def generate_image(selections: dict[str, str]) -> str:
    width, height = 900, 520
    image = generate_ai_image(selections, width, height)

    unique_id = uuid.uuid4().hex
    filename = f"smile_{unique_id}.png"
    filepath = os.path.join(GENERATED_DIR, filename)
    image.convert("RGB").save(filepath, format="PNG")
    return f"generated/{filename}"


def generate_ai_image(selections: dict[str, str], width: int, height: int) -> Image.Image:
    api_key = os.environ.get("SMILE_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing SMILE_IMAGE_API_KEY or OPENAI_API_KEY for AI image generation.")

    model = os.environ.get("SMILE_IMAGE_MODEL", "gpt-image-1")
    prompt = build_prompt(selections)
    payload = {
        "model": model,
        "prompt": prompt,
        "size": os.environ.get("SMILE_IMAGE_SIZE", "1024x1024"),
        "response_format": "b64_json",
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


def build_prompt(selections: dict[str, str]) -> str:
    actors = selections["actors"]
    activities = selections["activities"]
    areas = selections["areas"]
    accessories = selections["accessories"]
    return (
        "Create a highly detailed, cinematic, joyful illustration. "
        f"Scene: {actors} {activities} {areas} with {accessories}. "
        "Use a warm, whimsical palette, dynamic action, and strong character expressions. "
        "Ensure the scene clearly shows the actors, activity, area, and accessory."
    )


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
    try:
        image_path = generate_image(selections)
    except RuntimeError as exc:
        return render_template("image.html", error=str(exc), selections=selections)
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
