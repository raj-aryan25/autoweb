# Entry points

Configuration validation is available through `python -m autoweb.config` and `autoweb-config`.

Password login runs through `python -m autoweb.login`, `autoweb-login`, or this folder's thin `run_login.py` wrapper. See [the login documentation](../src/autoweb/login/README.md) for configuration, session reuse, and a controlled local demo.

The independent Gmail diagnostic runner follows in Phase IV. Keep these scripts thin; reusable behavior belongs in `src/autoweb/`.
