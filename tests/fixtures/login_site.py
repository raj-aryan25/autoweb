"""Compatibility imports for the reusable, controlled password website."""

from autoweb.testing.password_site import (
    DEMO_PASSWORD, DEMO_USERNAME, Handler, LoginSite, PROTECTED_HTML,
    login_html, main, make_server,
)

if __name__ == "__main__":
    main()
