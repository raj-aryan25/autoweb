"""Browser failures with safe messages that omit page contents and input values."""


class BrowserError(RuntimeError):
    """Base class for browser lifecycle and interaction failures."""


class BrowserStartupError(BrowserError):
    def __init__(self, resource: str, reason: str = "startup_failed"):
        self.resource = resource
        self.reason = reason
        super().__init__(f"Could not create browser resource: {resource} ({reason})")


class BrowserCleanupError(BrowserError):
    def __init__(self):
        super().__init__("Browser resource cleanup failed")


class BrowserActionError(BrowserError):
    def __init__(self, action: str, reason: str):
        self.action = action
        self.reason = reason
        super().__init__(f"Browser action {action} failed: {reason}")


class BrowserActionTimeout(BrowserActionError):
    def __init__(self, action: str):
        super().__init__(action, "timeout")
