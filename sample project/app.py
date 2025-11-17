"""Minimal Flask API that serves a rendered circle image."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, send_file

from circle_demo.draw import draw_circle

DEFAULT_RADIUS = 100
DEFAULT_COLOR = "#228be6"
ARTIFACT_PATH = Path("artifacts/circle.png")


def ensure_circle_image() -> Path:
    """Generate the circle image if it does not already exist."""
    if not ARTIFACT_PATH.exists():
        draw_circle(DEFAULT_RADIUS, DEFAULT_COLOR, ARTIFACT_PATH)
    return ARTIFACT_PATH


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():  # pragma: no cover - trivial route
        return jsonify(status="ok")

    @app.get("/circle")
    def serve_circle():
        image_path = ensure_circle_image()
        return send_file(image_path, mimetype="image/png")

    return app


app = create_app()
