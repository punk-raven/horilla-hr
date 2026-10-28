"""Django's {# #} comment only works on a single line. One that spans lines
is not a comment at all: the text, braces included, renders into the page."""

import re
from pathlib import Path

from django.conf import settings
from django.template import engines
from django.test import SimpleTestCase

UNCLOSED_COMMENT = re.compile(r"\{#(?:(?!#\}).)*$", re.MULTILINE)


class TemplateCommentTests(SimpleTestCase):
    def test_no_template_has_a_multi_line_hash_comment(self):
        offenders = []
        root = Path(settings.BASE_DIR).resolve()
        template_dirs = {
            Path(d).resolve()
            for loader in engines["django"].engine.template_loaders
            # The database template loader has no directories.
            for d in getattr(loader, "get_dirs", list)()
        }
        paths = {
            path
            for directory in template_dirs
            if directory.is_relative_to(root)
            for path in directory.rglob("*.html")
        }
        for path in sorted(paths):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in UNCLOSED_COMMENT.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(root)}:{line}")

        self.assertEqual(
            offenders, [], "Use {% comment %} for comments that span lines"
        )
