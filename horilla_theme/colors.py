"""
Colour helpers for the company themes.

A theme's primary_600 is the brand colour as designed: it is used for text,
borders, rings and tints as well as for the fill of primary buttons. Many
stock shades are too light to carry a white label (Ocean Blue's #3b82f6 is
3.7:1, Forest Green's #22c55e 2.3:1, where WCAG AA asks for 4.5:1), and the
same shade cannot simply be darkened in place: it also has to read on the
dark theme's panels, where a darker shade would fail instead.

So filled controls with white text use their own shade, derived from
primary_600: the same hue deepened towards black in 5% steps until a white
label reaches AA. Shades that already pass are kept as they are. Ocean
Blue's comes out at #326ed1 (85%), the blue of the sign-in button and the
mails (base/email_branding.py BRAND_BLUE).
"""

import re

WCAG_AA = 4.5
WHITE = "#ffffff"

# How much of the fill is kept on hover: one more 15% step towards black,
# so hover is always distinct from the fill and darker, hence still AA.
HOVER_KEEP = 0.85

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def parse_hex(value):
    """(r, g, b) in 0-255 for "#rgb" or "#rrggbb", or None if not a hex colour."""
    match = _HEX.match(str(value or "").strip())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4))


def to_hex(rgb):
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


def relative_luminance(rgb):
    def linear(channel):
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first, second):
    """WCAG contrast ratio of two hex colours, from 1 to 21."""
    high, low = sorted(
        (relative_luminance(parse_hex(first)), relative_luminance(parse_hex(second))),
        reverse=True,
    )
    return (high + 0.05) / (low + 0.05)


def deepen(rgb, keep):
    """The colour mixed with black, keeping `keep` (0-1) of it, like CSS
    color-mix(in srgb, <colour> <keep>%, black)."""
    return tuple(round(channel * keep) for channel in rgb)


def white_text_fill(value):
    """The shade of `value` to fill a control that has a white label.

    Returns `value` unchanged when it already passes, or is not a hex colour
    (a template default, an empty field).
    """
    rgb = parse_hex(value)
    if rgb is None:
        return value
    for percent in range(100, 0, -5):
        candidate = deepen(rgb, percent / 100)
        if contrast_ratio(to_hex(candidate), WHITE) >= WCAG_AA:
            return value if percent == 100 else to_hex(candidate)
    return "#000000"


def white_text_fill_hover(value):
    """The hover/active shade for white_text_fill(value): a step darker."""
    fill = white_text_fill(value)
    rgb = parse_hex(fill)
    if rgb is None:
        return value
    return to_hex(deepen(rgb, HOVER_KEEP))
