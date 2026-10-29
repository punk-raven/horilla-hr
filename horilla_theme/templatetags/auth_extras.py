"""Filters for the pages shown before sign-in."""

from datetime import timedelta

from django import template
from django.utils import timezone
from django.utils.timesince import timesince

register = template.Library()


@register.filter
def duration_words(value):
    """A timedelta in words, e.g. "30 minutes" or "1 hour, 30 minutes"."""
    if not isinstance(value, timedelta) or value <= timedelta(0):
        return ""
    now = timezone.now()
    return timesince(now, now + value)
