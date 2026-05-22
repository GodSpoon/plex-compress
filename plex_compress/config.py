"""Configuration and defaults for plex_compress."""

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Config:
    """Transcoding configuration."""

    # Paths
    library_path: str = ""
    temp_dir: str = field(default_factory=lambda: os.path.expanduser("~/tmp/plex_compress"))
    state_db_path: str = field(default_factory=lambda: os.path.expanduser("~/.plex_compress/state.db"))
    log_path: Optional[str] = None

    # Video settings
    video_encoder: str = "libx265"
    # Quality: CRF 0-51 for libx265, 0-100 scale for VideoToolbox, CQ 0-51 for hevc_nvenc
    video_quality: int = 28
    video_tag: str = "hvc1"
    video_profile: str = "main"
    # Preset: libx265 uses ultrafast..veryslow; NVENC uses p1..p7 or slow/medium/fast;
    # VideoToolbox ignores preset.
    video_preset: str = "medium"
    video_allow_sw: bool = True

    # Audio settings
    audio_encoder: str = "aac"  # Use ffmpeg built-in AAC
    audio_bitrate: str = "160k"
    audio_channels: int = 2
    audio_layout: str = "stereo"
    loudnorm_i: float = -16.0   # LUFS target
    loudnorm_tp: float = -1.5   # True peak limit dBTP
    loudnorm_lra: float = 11.0  # Loudness range

    # Downmix: built-in ATSC (-ac 2) is default; set to True for RFC 7845 pan filter
    use_rfc7845_downmix: bool = False

    # Container
    output_container: str = "mkv"

    # Skip thresholds
    min_file_size_mb: float = 200.0
    min_duration_seconds: float = 300.0  # 5 minutes
    skip_codecs_video: List[str] = field(default_factory=lambda: ["hevc", "h265", "av1"])
    skip_codecs_audio: List[str] = field(default_factory=lambda: ["aac"])

    # Processing
    parallel_jobs: int = 1
    keep_backup: bool = False
    backup_suffix: str = ".plex_compress_backup"
    dry_run: bool = False
    verbose: bool = False

    # Limits
    limit: Optional[int] = None  # Max files to process
    exclusions: List[str] = field(default_factory=list)

    # Safety
    verify_output: bool = True
    verify_duration_tolerance: float = 2.0  # seconds
    verify_checksum: bool = True

    def __post_init__(self):
        if self.library_path:
            self.library_path = os.path.abspath(self.library_path)
        self.temp_dir = os.path.abspath(self.temp_dir)
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.state_db_path), exist_ok=True)
