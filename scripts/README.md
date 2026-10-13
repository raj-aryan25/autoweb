# Entry points

Configuration validation is available through `python -m autoweb.config` and `autoweb-config`.

Password login runs through `python -m autoweb.login`, `autoweb-login`, or this folder's thin `run_login.py` wrapper. See [the login documentation](../src/autoweb/login/README.md) for configuration, session reuse, and a controlled local demo.

Independent Gmail diagnostics run through `python -m autoweb.gmail`, `autoweb-gmail`, or `scripts/read_verification.py`. Use `--authorize` for explicit OAuth setup, or `--site` for fresh-mail polling. Codes stay hidden unless `--show-code` is selected; diagnostics never perform website actions or mark messages read. See [Gmail setup and usage](../src/autoweb/gmail/README.md). Keep these scripts thin; reusable behavior belongs in `src/autoweb/`.
