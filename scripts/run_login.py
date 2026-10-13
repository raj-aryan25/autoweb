"""Delegate to the reusable login CLI; install the package in conda autoweb first."""

from autoweb.login.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
