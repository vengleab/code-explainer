"""
backend/render/ — turn traced facts into pixels.

pysyntax.py — what kind of token is this?
theme.py    — what color is that token / line state?
canvas.py   — how do pixels get drawn (PIL), and where the bundled fonts live
panels.py   — what does one card look like?
composer.py — where do the cards go?
fonts/      — bundled Roboto Mono; resolved relative to canvas.py, so the two
              must stay in the same directory.

Deliberately import-free: importing composer here would cycle with composer's
own `from . import panels`.
"""
