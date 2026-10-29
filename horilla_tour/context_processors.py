"""
Global template context for the tour engine.

Exposes a lightweight flag so the base template can render the "Help / Take a
tour" launcher (and an optional "pending" dot) without each page having to
know about tours. The heavy lifting — resolving which tour/steps apply — is
done lazily by the JS controller via the ``tour-active`` API.
"""

import logging

logger = logging.getLogger(__name__)


def pending_tours_flag(request):
    """Return launcher availability + whether the user has an unfinished tour."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"tour_launcher_enabled": False, "tour_has_pending": False}

    has_pending = False
    try:
        from horilla_tour.models import Tour, TourProgress

        # Published tours visible to the user's company (+ global) that are
        # auto-start and not yet shown to this user (no progress row at all,
        # matching the tour-active API's auto_start rule).
        published = Tour.objects.filter(is_active=True, is_published=True)
        if published.exists():
            seen_ids = TourProgress.objects.filter(user=user).values_list(
                "tour_id", flat=True
            )
            has_pending = (
                published.filter(trigger="auto_once").exclude(id__in=seen_ids).exists()
            )
    except Exception as exc:  # never let the launcher break a page render
        logger.debug("pending_tours_flag skipped: %s", exc)

    return {"tour_launcher_enabled": True, "tour_has_pending": has_pending}
