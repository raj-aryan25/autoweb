"""Test support without a pytest dependency in the login engine."""

from .verification import FakeVerificationProvider

__all__ = ["FakeVerificationProvider"]
