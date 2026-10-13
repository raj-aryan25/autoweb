"""Configuration failures that can be reported without exposing input values."""


class ConfigurationError(ValueError):
    """Settings, profile, or credential references are invalid."""
