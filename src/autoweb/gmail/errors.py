"""Static, secret-safe errors; provider exceptions and response bodies stay private."""


class GmailError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Gmail verification failed: {reason}")


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
