"""Static, secret-safe errors; provider exceptions and response bodies stay private."""

from autoweb.contracts import VerificationError


class GmailError(VerificationError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.args = (f"Gmail verification failed: {reason}",)


class GmailAuthenticationError(GmailError):
    pass


class GmailAPIError(GmailError):
    def __init__(self, reason: str, *, retryable: bool = False):
        self.retryable = retryable
        super().__init__(reason)


class GmailMessageMissing(GmailAPIError):
    pass


class VerificationTimeout(GmailError):
    def __init__(self):
        super().__init__("verification_timeout")
