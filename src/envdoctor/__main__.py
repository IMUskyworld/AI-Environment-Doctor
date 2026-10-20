"""Allow ``python -m envdoctor`` in addition to the ``envdoctor`` console script."""

from __future__ import annotations

from envdoctor.cli.main import app

if __name__ == "__main__":
    app()
