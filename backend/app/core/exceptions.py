from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

class AppException(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)

class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404)

class AuthException(AppException):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status_code=401)

class ForbiddenException(AppException):
    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, status_code=403)

class BusinessException(AppException):
    def __init__(self, message: str):
        super().__init__(message, status_code=400)

async def app_exception_handler(request: Request, exc: AppException):
    logger.warning(f"AppException: {exc.message} (status: {exc.status_code}) on {request.url.path}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "code": exc.status_code, "message": exc.message}
    )

async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.url.path}: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"success": False, "code": 500, "message": "Internal Server Error"}
    )
