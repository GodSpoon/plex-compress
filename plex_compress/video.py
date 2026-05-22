"""Video encoder settings builder."""

from .config import Config


def build_video_encoder_args(cfg: Config) -> list:
    """Build ffmpeg video encoder argument list."""
    args = ["-c:v", cfg.video_encoder]

    if cfg.video_encoder == "libx265":
        args.extend([
            "-crf", str(cfg.video_quality),
            "-preset", cfg.video_preset,
            "-tag:v", cfg.video_tag,
            "-profile:v", cfg.video_profile,
            "-x265-params", "log-level=warning",
        ])
    elif cfg.video_encoder.endswith("videotoolbox"):
        args.extend([
            "-q:v", str(cfg.video_quality),
            "-tag:v", cfg.video_tag,
            "-profile:v", cfg.video_profile,
        ])
        if cfg.video_allow_sw:
            args.extend(["-allow_sw", "1"])
    elif cfg.video_encoder == "hevc_nvenc":
        # Turing (RTX 20-series) and newer support B-frames for HEVC NVENC
        args.extend([
            "-preset", cfg.video_preset,
            "-cq", str(cfg.video_quality),
            "-pix_fmt", "yuv420p",
            "-tag:v", cfg.video_tag,
            "-profile:v", cfg.video_profile,
            "-bf", "4",
            "-rc", "vbr",
            "-b:v", "0",
        ])
    else:
        args.extend(["-q:v", str(cfg.video_quality)])

    return args
