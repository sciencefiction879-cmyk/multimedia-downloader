"""
Custom exception hierarchy for MultiDownloader.
"""


class MultiDownloaderError(Exception):
    """Base exception for all MultiDownloader application errors."""
    pass


class NetworkError(MultiDownloaderError):
    """Raised when a network operation fails."""
    pass


class ValidationError(MultiDownloaderError):
    """Raised when URL or parameter validation fails."""
    pass


class MetadataError(MultiDownloaderError):
    """Raised when fetching video or channel metadata fails."""
    pass


class DownloadError(MultiDownloaderError):
    """Raised when downloading a media stream fails."""
    pass


class TranscodeError(MultiDownloaderError):
    """Raised when FFmpeg conversion or extraction fails."""
    pass


class TranscriptError(MultiDownloaderError):
    """Raised when fetching transcripts or subtitles fails."""
    pass


class StorageError(MultiDownloaderError):
    """Raised when filesystem write, read, or zip operations fail."""
    pass
