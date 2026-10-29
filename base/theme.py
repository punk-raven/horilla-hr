"""
The user's light or dark theme choice.

It lives in a long-lived cookie, not the session, so it survives logout and
is readable on the sign-in pages too. No cookie means "follow the system":
the inline script in horilla_theme/components/theme_init.html then applies
the OS preference (prefers-color-scheme) before the page paints.
"""

THEME_COOKIE_NAME = "horilla_theme"
THEME_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # one year, in seconds
THEMES = ("dark", "light")
FOLLOW_SYSTEM = "system"

# Where the choice was kept before the cookie. Read once, then migrated to the
# cookie by base.middleware.ThemePreferenceMiddleware.
LEGACY_SESSION_KEY = "theme"


def get_session_theme(request):
    """The theme kept in the session by older versions, or None."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    theme = session.get(LEGACY_SESSION_KEY)
    return theme if theme in THEMES else None


def get_theme_preference(request):
    """
    The explicit theme the user picked: "dark", "light", or None to follow
    the system.
    """
    theme = request.COOKIES.get(THEME_COOKIE_NAME)
    if theme in THEMES:
        return theme
    return get_session_theme(request)


def set_theme_cookie(request, response, theme):
    """Remember an explicit theme choice on the response."""
    response.set_cookie(
        THEME_COOKIE_NAME,
        theme,
        max_age=THEME_COOKIE_MAX_AGE,
        path="/",
        samesite="Lax",
        secure=request.is_secure(),
        httponly=True,
    )


def clear_theme_cookie(response):
    """Forget the explicit choice, so the theme follows the system again."""
    response.delete_cookie(THEME_COOKIE_NAME, path="/", samesite="Lax")
