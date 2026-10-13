"""Check site configuration without opening a browser or contacting providers."""

import argparse

from .errors import ConfigurationError
from .loader import load_configuration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, help="Site JSON path, relative to the project root")
    parser.add_argument("--settings", default="configs/settings.json")
    parser.add_argument("--project-root", help="Override the repository root")
    parser.add_argument("--env-file", help="Explicit dotenv file, relative to the project root")
    parser.add_argument("--schema-only", action="store_true", help="Skip secret resolution; not a login readiness check")
    args = parser.parse_args(argv)
    try:
        configuration = load_configuration(
            args.site, args.settings, project_root=args.project_root,
            env_file=args.env_file, schema_only=args.schema_only,
        )
    except ConfigurationError as error:
        parser.exit(2, f"Configuration error: {error}\n")
    mode = "schema" if args.schema_only else "configuration and credential references"
    print(f"Valid {mode}: {configuration.site.name} ({configuration.site.login_mode})")
    print(f"Steps: {len(configuration.site.steps)}; browser: {configuration.settings.browser.engine}")
    return 0
