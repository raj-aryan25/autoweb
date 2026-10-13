"""Thin Gmail diagnostic entry point; run from the installed autoweb environment."""

from autoweb.gmail.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
