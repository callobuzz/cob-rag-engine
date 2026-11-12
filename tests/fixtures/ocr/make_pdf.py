"""Wrap scanned-hindi.png in an image-only PDF (no text layer), like a real scan."""

from pathlib import Path

from PIL import Image

here = Path(__file__).parent
Image.open(here / "scanned-hindi.png").convert("RGB").save(
    here / "scanned-hindi.pdf", resolution=150.0
)
