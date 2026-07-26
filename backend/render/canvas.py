"""
backend/render/canvas.py — the drawing primitive layer (UI).

`Canvas` is a thin wrapper over a PIL Image + ImageDraw. Panels (panels.py) draw
through it using named methods (text/rect/rounded_rect/line/text_width) instead
of touching PIL directly, so the panel code reads as intent ("draw a rounded
panel here") rather than PIL bookkeeping, and so there is one place to change if
the rendering backend ever does.

Fonts are bundled in backend/render/fonts/ (Roboto Mono, OFL) and loaded here so the
GIFs never depend on system fonts. Per-mode font *sets* (which sizes each
visualizer uses) live in the composers, since sizing is a layout decision.
"""
import os

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
MONO = os.path.join(FONT_DIR, "RobotoMono-Regular.ttf")
MONO_B = os.path.join(FONT_DIR, "RobotoMono-Bold.ttf")


def load_font(path, size):
    """Load a bundled TrueType font, falling back to PIL's default on failure."""
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


class Canvas:
    """A drawable RGB image. Construct with a size + background, draw, then read `.image`."""

    def __init__(self, width, height, background):
        self.image = Image.new("RGB", (width, height), background)
        self._draw = ImageDraw.Draw(self.image)

    def text(self, xy, text, font, fill):
        self._draw.text(xy, text, font=font, fill=fill)

    def text_width(self, text, font):
        return self._draw.textlength(text, font=font)

    def rect(self, box, fill):
        self._draw.rectangle(box, fill=fill)

    def rounded_rect(self, box, radius, fill=None, outline=None, width=1):
        self._draw.rounded_rectangle(box, radius, fill=fill, outline=outline, width=width)

    def line(self, coords, fill, width=1):
        self._draw.line(coords, fill=fill, width=width)
