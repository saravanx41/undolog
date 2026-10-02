"""undolog-core exceptions."""


class UndologError(Exception):
    """Base class for all undolog-core errors."""


class FrozenError(UndologError):
    """Raised when a side effect is attempted on a frozen thread.

    Also raised by freeze() itself when another session already holds the
    freeze (try-lock semantics: a second concurrent freeze is a clear error,
    never a silent block).
    """
