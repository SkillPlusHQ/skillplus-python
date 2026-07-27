from __future__ import annotations


class SkillPlusError(Exception):
    """Error raised by the SkillPlus SDK."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: object | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail
