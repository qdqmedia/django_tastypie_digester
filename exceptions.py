"""Exceptions"""


class BaseException(Exception):
    """Base exception for the client"""

    def __init__(self, message=None, **kw):
        for key, value in kw.items():
            setattr(self, key, value)
        super(BaseException, self).__init__(message)


class ApiError(BaseException):
    """Raised by the Client"""


class ResourceTypeMissing(ApiError):
    """Resource type is missing"""


class ResourceIdMissing(ApiError):
    """Resource ID is missing"""


class TooManyResources(ApiError):
    """Too many resources found"""


class HttpError(BaseException):
    """HTTP error"""


class BadHttpStatus(HttpError):
    """Invalid HTTP status"""
