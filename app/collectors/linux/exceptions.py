"""Safe Linux collector exceptions that never contain remote output."""


class LinuxCollectorError(RuntimeError):
    """Base error for Linux collection failures."""


class LinuxCollectorValidationError(LinuxCollectorError):
    """Raised when collector input or parsed data is invalid."""


class LinuxCommandError(LinuxCollectorError):
    """Raised when a remote command cannot provide usable output."""


class LinuxParserError(LinuxCollectorError):
    """Raised when bounded command output cannot be parsed safely."""
