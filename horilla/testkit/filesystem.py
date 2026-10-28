"""Filesystem helpers for tests that must not write outside temp storage."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile


@contextlib.contextmanager
def read_only_cwd():
    """
    Run the block with a read-only, empty working directory.

    Mirrors the production image, where the app runs as a non-root user that
    cannot write to /app. Code that writes scratch files to a relative path
    raises PermissionError here. As root the chmod is not enforced, so the
    directory is also checked for leftovers on exit.
    """
    previous = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="horilla-ro-cwd-") as tmp:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IXUSR)
        os.chdir(tmp)
        try:
            yield tmp
            leftovers = os.listdir(tmp)
            if leftovers:
                raise AssertionError(
                    f"wrote to the working directory: {sorted(leftovers)}"
                )
        finally:
            os.chdir(previous)
            os.chmod(tmp, stat.S_IRWXU)
