from __future__ import annotations


class MediaContractError(RuntimeError):
    def __init__(self, message: str, detail: dict | None = None, *, retryable: bool = False):
        super().__init__(message)
        self.detail = dict(detail or {})
        self.retryable = bool(retryable)
