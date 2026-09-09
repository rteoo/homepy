"""Entry point for ``python -m homepy``."""

from .cli import main


if __name__ == "__main__":  # pragma: no cover - exercised by the interpreter
    raise SystemExit(main())
