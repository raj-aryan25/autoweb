"""Login failures with structured outcomes and no credential values."""

from .results import LoginResult


class LoginError(RuntimeError):
    def __init__(self, result: LoginResult):
        self.result = result
        super().__init__(
            f"Login failed for {result.site} during {result.failed_step}: {result.reason}"
        )
