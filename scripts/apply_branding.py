#!/usr/bin/env python3
"""
Replace the login logo and the browser-tab icons with your own, from ONE image.

    python scripts/apply_branding.py /path/to/logo.png            # square-ish icon or full logo, PNG
    python scripts/apply_branding.py logo.png --favicon icon.png  # optional separate square icon for the tab

Writes (inside this repo, overwriting Horilla's):
    static/images/ui/auth-logo.png          login / error pages / emails logo (fits 400x140, transparent)
    static/favicons/favicon-16x16.png, favicon-32x32.png, apple-touch-icon.png (180),
    android-chrome-192x192.png, android-chrome-512x512.png, favicon.ico (16/32/48)

Then on the server:  python manage.py collectstatic --noinput   and restart the app; hard-refresh the browser (Ctrl+F5).
Needs Pillow (already installed with Horilla).
"""

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FAV = ROOT / "static" / "favicons"
LOGO = ROOT / "static" / "images" / "ui" / "auth-logo.png"


def square(img, size):
    """Fit the image inside a transparent square without distorting it."""
    img = img.copy()
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    return canvas


def main():
    p = argparse.ArgumentParser()
    p.add_argument("logo")
    p.add_argument("--favicon", help="optional square icon for the browser tab (defaults to the logo)")
    args = p.parse_args()
    try:
        logo = Image.open(args.logo).convert("RGBA")
        icon = Image.open(args.favicon).convert("RGBA") if args.favicon else logo
    except OSError as exc:
        sys.exit(f"Cannot read the image: {exc}")
    if min(logo.size) < 64:
        sys.exit("The logo is too small. Use an image at least 256 px on its longer side (PNG with transparent background is best).")

    wide = logo.copy()
    wide.thumbnail((400, 140), Image.LANCZOS)
    LOGO.parent.mkdir(parents=True, exist_ok=True)
    wide.save(LOGO)
    FAV.mkdir(parents=True, exist_ok=True)
    for name, size in (("favicon-16x16.png", 16), ("favicon-32x32.png", 32), ("apple-touch-icon.png", 180),
                       ("android-chrome-192x192.png", 192), ("android-chrome-512x512.png", 512)):
        square(icon, size).save(FAV / name)
    square(icon, 48).save(FAV / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("Branding files written. Now run: python manage.py collectstatic --noinput, restart the app, and hard-refresh the browser.")


if __name__ == "__main__":
    main()
