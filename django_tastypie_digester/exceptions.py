

class BaseException(Exception):
    """Base exception for the client"""

    def __init__(self, message='', response=None):
        super(BaseException, self).__init__(message)
        self.response = response

    @property
    def status(self):
        return self.response.status_code


class ApiError(BaseException):
    """Raised by the Client"""


class ResourceIdMissing(ApiError):
    """Resource ID is missing"""


class TooManyResources(ApiError):
    """Too many resources found"""


class HttpError(BaseException):
    """HTTP error"""


class BadHttpStatus(HttpError):
    """Invalid HTTP status"""


class ResourceDeleted(ApiError):
    """Resource has been deleted - some operations are forbidden"""
