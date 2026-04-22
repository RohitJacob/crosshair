"""Allow `python -m crosshair ...` as an alternative to the `crosshair` script."""

from crosshair.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
