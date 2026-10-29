"""
Production security gates and Django secure defaults.

Applied from base settings when DEBUG is False or HORILLA_ENV=production.
Local/tutorial defaults (DEBUG=True) remain permissive for open-source onboarding.
"""

from django.core.exceptions import ImproperlyConfigured

# Known insecure values shipped in examples / historical defaults.
INSECURE_SECRET_KEYS = frozenset(
    {
        "django-insecure-default-key",
        "dev-secret-key-change-in-production",
        "change-me",
        "django-insecure-j8op9)1q8$1&0^s&p*_0%d#pr@w9qj@1o=3#@d=a(^@9@zd@%j",
    }
)

INSECURE_DB_INIT_PASSWORDS = frozenset(
    {
        "d3f6a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d",
        "dev-init-password-change-in-production",
    }
)


def is_insecure_secret_key(secret_key) -> bool:
    """True for an empty key, a shipped default, or a placeholder value."""
    return (
        not secret_key
        or secret_key in INSECURE_SECRET_KEYS
        or secret_key.startswith(("django-insecure-", "change-me"))
    )


def resolve_secret_key(secret_key, generated_key_file):
    """
    Return the SECRET_KEY every process of this install must sign with.

    The Docker entrypoint replaces a missing or public default SECRET_KEY with
    a random one persisted at ``generated_key_file``. Reading that file here,
    rather than relying on the entrypoint exporting it, is what makes
    ``docker exec ... manage.py shell`` and the scheduler agree with gunicorn:
    an exported variable only reaches the entrypoint's own child, while
    ``docker exec`` starts from the container's configured environment and
    would sign with the placeholder instead.

    A real key in the environment always wins. Without one, and without a
    persisted key, the configured value is returned unchanged so the
    validate_production_secrets() still fails loudly.
    """
    if not is_insecure_secret_key(secret_key):
        return secret_key
    try:
        with open(generated_key_file, encoding="utf-8") as fh:
            generated = fh.read().strip()
    except OSError:
        return secret_key
    return generated or secret_key


def is_production_mode(debug: bool, horilla_env: str) -> bool:
    return (not debug) or (horilla_env or "").strip().lower() == "production"


def validate_production_secrets(secret_key, allowed_hosts, db_init_password):
    """Raise ImproperlyConfigured when production uses unsafe defaults."""
    errors = []

    if not secret_key or secret_key in INSECURE_SECRET_KEYS:
        errors.append(
            "SECRET_KEY is missing or uses a known insecure default. "
            "Generate one with: python -c "
            '"from django.core.management.utils import get_random_secret_key; '
            'print(get_random_secret_key())"'
        )
    elif secret_key.startswith("django-insecure-") or secret_key.startswith(
        "change-me"
    ):
        errors.append(
            "SECRET_KEY still uses a placeholder value (django-insecure-* or "
            "change-me*). Set a unique SECRET_KEY before running with DEBUG=False."
        )

    hosts = list(allowed_hosts or [])
    if not hosts or hosts == ["*"] or set(hosts) == {"*"}:
        errors.append(
            "ALLOWED_HOSTS must be set to your real hostnames in production "
            '(not "*" or empty).'
        )

    if not db_init_password or db_init_password in INSECURE_DB_INIT_PASSWORDS:
        errors.append(
            "DB_INIT_PASSWORD must be changed from the documented default "
            "before running in production."
        )

    if errors:
        raise ImproperlyConfigured(
            "Horilla production security check failed:\n- " + "\n- ".join(errors)
        )


def apply_secure_defaults(env, debug: bool) -> dict:
    """
    Return Django security-related settings for non-DEBUG deployments.

    SECURE_SSL_REDIRECT / HSTS stay off unless SECURE_SSL_REDIRECT=true so
    plain-HTTP Docker + nginx :80 installs keep working.
    """
    settings = {
        "SESSION_COOKIE_SECURE": True,
        "CSRF_COOKIE_SECURE": True,
        "SECURE_CONTENT_TYPE_NOSNIFF": True,
        "SECURE_REFERRER_POLICY": "strict-origin-when-cross-origin",
        "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
        "SECURE_SSL_REDIRECT": env.bool("SECURE_SSL_REDIRECT", default=False),
    }
    if settings["SECURE_SSL_REDIRECT"]:
        settings["SECURE_HSTS_SECONDS"] = env.int(
            "SECURE_HSTS_SECONDS", default=31536000
        )
        settings["SECURE_HSTS_INCLUDE_SUBDOMAINS"] = True
        settings["SECURE_HSTS_PRELOAD"] = True
    return settings
