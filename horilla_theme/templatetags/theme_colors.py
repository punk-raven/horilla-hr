"""Theme colour filters: the shades filled controls use (see horilla_theme.colors)."""

from django import template

from horilla_theme.colors import white_text_fill, white_text_fill_hover

register = template.Library()


@register.filter
def button_fill(value):
    """{{ theme.primary_600|button_fill }}: the fill a white label reads on."""
    return white_text_fill(value)


@register.filter
def button_fill_hover(value):
    """{{ theme.primary_600|button_fill_hover }}: its hover/active shade."""
    return white_text_fill_hover(value)
