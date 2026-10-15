"""Entry point so the package can be executed with ``python -m kicad_ai``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())