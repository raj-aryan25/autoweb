"""Independent Gmail verification; imports never authenticate or create clients."""

from .client import GmailClient
from .errors import GmailAPIError, GmailAuthenticationError, GmailError, VerificationTimeout
from .poller import GmailVerificationProvider

__all__ = ["GmailClient", "GmailVerificationProvider", "GmailError", "GmailAPIError",
           "GmailAuthenticationError", "VerificationTimeout"]
