class DomainError(Exception):
    """业务域错误，统一由 FastAPI 转换为 HTTP 422。"""

    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra
