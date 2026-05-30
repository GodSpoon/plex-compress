"""Plex Compress: transcode Plex libraries to space-efficient HEVC with stereo audio."""

__version__ = "0.1.0"
__all__ = ["config", "probe", "scanner", "audio", "video", "transcoder", "state", "utils", "cli", "health", "watch", "intelligence"]


class PlexCompressError(Exception):
    """Base exception for plex_compress."""


class VerificationError(PlexCompressError):
    """Output file failed verification."""


class TranscodeError(PlexCompressError):
    """Transcoding process failed."""


class ProbeError(PlexCompressError):
    """ffprobe failed or returned unexpected data."""


class StateError(PlexCompressError):
    """Database state operation failed."""


class SkipFileError(PlexCompressError):
    """File should be skipped."""


class NotCandidateError(PlexCompressError):
    """File is not a candidate for transcoding."""


class AlreadyOptimalError(NotCandidateError):
    """File is already in target format."""


class FfmpegError(TranscodeError):
    """ffmpeg process returned non-zero exit code."""


class FfprobeError(ProbeError):
    """ffprobe process returned non-zero exit code."""


class ReplaceError(PlexCompressError):
    """Atomic replacement failed."""


class CopyError(PlexCompressError):
    """File copy to/from temp location failed."""


class FilterError(PlexCompressError):
    """Audio or video filter string is invalid."""


class MetadataError(ProbeError):
    """Required metadata missing from probe."""


class HardwareError(TranscodeError):
    """Hardware encoder initialization failed."""


class InterruptError(PlexCompressError):
    """Operation interrupted by user signal."""


class ConfigError(PlexCompressError):
    """Invalid configuration."""


class LimitError(PlexCompressError):
    """Processing limit reached."""


class DryRunError(PlexCompressError):
    """Operation blocked because dry-run mode is active."""


class TempSpaceError(PlexCompressError):
    """Insufficient temp space for transcoding."""


class SafetyError(PlexCompressError):
    """Safety check prevented destructive operation."""


class NormalizeError(PlexCompressError):
    """Audio normalization analysis failed."""


class SubtitleLossError(VerificationError):
    """Expected subtitle stream missing from output."""


class AudioLossError(VerificationError):
    """Expected audio stream missing from output."""


class AttachmentLossError(VerificationError):
    """Expected attachment stream missing from output."""


class ChapterLossError(VerificationError):
    """Expected chapter missing from output."""

class ContainerError(VerificationError):
    """Output container format verification failed."""


class BitrateError(ProbeError):
    """Bitrate calculation or probe failed."""


class ChecksumError(VerificationError):
    """File checksum mismatch after copy."""


class DurationError(ProbeError):
    """Duration mismatch between source and output."""


class CodecMismatchError(VerificationError):
    """Output codec does not match expected target codec."""


class ChannelLayoutError(VerificationError):
    """Output channel layout does not match expected stereo."""


class StreamMapError(PlexCompressError):
    """Stream mapping failed or is ambiguous."""


class NetworkError(PlexCompressError):
    """Network-related file operation failed."""


class ProgressError(PlexCompressError):
    """Progress reporting failed."""


class ReportError(PlexCompressError):
    """Scan report generation failed."""


class StatsError(PlexCompressError):
    """Statistics calculation failed."""


class ResumeError(StateError):
    """Resume state is inconsistent or corrupted."""


class DuplicateError(StateError):
    """Duplicate entry in state database."""


class LockError(StateError):
    """File lock acquisition failed."""


class UnlockError(StateError):
    """File lock release failed."""


class PermissionError(PlexCompressError):
    """Permission denied for file operation."""


class TimeoutError(PlexCompressError):
    """Operation timed out."""
