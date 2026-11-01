"""
The 500 handler and the CSRF failure view.

Django's default handler loads 500.html through the configured loaders, and the
first of those (horilla_dbtemplate) looks the template up in the database. A 500
is often the database failing, and then the error page failed too and the user
got a bare "Server Error (500)". This renders 500.html from the files alone.
"""

import logging

from django.contrib import messages
from django.http import HttpResponseServerError
from django.shortcuts import redirect
from django.template import Context, Engine, TemplateDoesNotExist
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.csrf import csrf_failure as django_csrf_failure
from django.views.decorators.csrf import requires_csrf_token

logger = logging.getLogger(__name__)

_engine = None


def _file_engine():
    """The project's template engine minus any loader that touches the database."""
    global _engine
    if _engine is None:
        default = Engine.get_default()
        loaders = []
        for loader in default.loaders:
            if isinstance(loader, (list, tuple)) and loader[0].endswith(
                ".cached.Loader"
            ):
                loaders.extend(loader[1])
            else:
                loaders.append(loader)
        loaders = [
            loader
            for loader in loaders
            if not (loader if isinstance(loader, str) else loader[0]).startswith(
                "horilla_dbtemplate."
            )
        ]
        _engine = Engine(
            loaders=loaders,
            libraries=default.libraries,
            builtins=[b for b in default.builtins if b not in Engine.default_builtins],
            debug=default.debug,
        )
    return _engine


@requires_csrf_token
def server_error(request, template_name="500.html"):
    try:
        body = _file_engine().get_template(template_name).render(Context({}))
    except TemplateDoesNotExist:
        return HttpResponseServerError(
            "<h1>Server Error (500)</h1>", content_type="text/html"
        )
    except Exception:  # never let the error page raise its own error
        logger.exception("Rendering %s failed", template_name)
        return HttpResponseServerError(
            "<h1>Server Error (500)</h1>", content_type="text/html"
        )
    return HttpResponseServerError(body)


def csrf_failure(request, reason=""):
    """
    Turn a stale sign-in form into a redirect instead of a bare 403.

    Signing in rotates the CSRF token, so any second submit of the same login
    page carries a token that is no longer valid: a double click or Enter while
    the dashboard is still loading, or Back and submit again. The first submit
    already signed the user in, yet the browser showed Django's 403 page. A
    login page left open past its token's life fails the same way.

    For POSTs to the login page only: a signed-in user goes on to ``next``, as
    the successful submit would have sent them; anyone else gets the login page
    again with a note. Neither path signs anyone in or changes state, so a
    forged cross-site login POST gains nothing. Every other CSRF failure keeps
    Django's 403.
    """
    if request.method == "POST" and request.path == reverse("login"):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            next_url = request.GET.get("next", "/")
            if not url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                next_url = "/"
            return redirect(next_url)
        messages.info(request, _("The sign-in page had expired. Please sign in again."))
        return redirect(request.get_full_path())
    return django_csrf_failure(request, reason=reason)
