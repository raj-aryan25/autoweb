"""Explicit structured reports without secrets or browser objects."""

from .json_report import login_report, write_login_report

__all__ = ["login_report", "write_login_report"]
