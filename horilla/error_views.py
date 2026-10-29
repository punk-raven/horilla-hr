"""
The 500 handler.

Django's default handler loads 500.html through the configured loaders, and the
first of those (horilla_dbtemplate) looks the template up in the database. A 500
is often the database failing, and then the error page failed too and the user
got a bare "Server Error (500)". This renders 500.html from the files alone.
"""

import logging

from django.http import HttpResponseServerError
from django.template import Context, Engine, TemplateDoesNotExist
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
