class AppException(Exception):
    """Base exception for all application-level errors."""
    
    def __init__(
        self,
        message: str,
        code: str = "internal_error",
        status_code: int = 500,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(self.message)


class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message=message, code="not_found", status_code=404)


class ValidationException(AppException):
    def __init__(self, message: str = "Validation error"):
        super().__init__(message=message, code="validation_error", status_code=422)


class ConflictException(AppException):
    """Business state conflict — action not allowed given current object state (HTTP 400)."""
    def __init__(self, message: str = "Conflict"):
        super().__init__(message=message, code="conflict", status_code=400)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message=message, code="unauthorized", status_code=401)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message=message, code="forbidden", status_code=403)


class CallbackRetryException(Exception):
    """Exception raised when a callback fails and should be retried by the worker."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)
