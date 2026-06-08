"""Audio filter and encoder settings builder."""

from .config import Config
from . import FilterError


def build_audio_filter(cfg: Config) -> str:
    """Build ffmpeg audio filter string for downmix + normalization.

    Order matters: downmix FIRST, then loudnorm. The reverse order (loudnorm
    on 6 channels, then downmix) wastes CPU on ebur128 analysis of channels
    that get discarded, and can produce measurably different gain values
    since EBU R128 weights channels differently.
    """
    filters = []

    if cfg.use_rfc7845_downmix:
        # RFC 7845 Opus downmix coefficients including LFE
        # This is layout-specific and only handles standard 5.1(side)
        # For robustness, we prefer the built-in aformat downmix in default mode
        pan = (
            "pan=stereo|"
            "FL=0.374107*FC+0.529067*FL+0.458186*BL+0.264534*BR+0.374107*LFE|"
            "FR=0.374107*FC+0.529067*FR+0.458186*BR+0.264534*BL+0.374107*LFE"
        )
        filters.append(pan)
    else:
        # Use ffmpeg's aformat downmix (faster than running loudnorm on 6
        # channels and downmixing after). Layout-agnostic.
        filters.append("aformat=channel_layouts=stereo")

    # EBU R128 loudnorm on the (now-stereo) signal
    loudnorm = f"loudnorm=I={cfg.loudnorm_i}:TP={cfg.loudnorm_tp}:LRA={cfg.loudnorm_lra}"
    filters.append(loudnorm)

    return ",".join(filters) if filters else ""


def build_audio_encoder_args(cfg: Config) -> list:
    """Build ffmpeg audio encoder argument list."""
    args = []
    audio_filter = build_audio_filter(cfg)

    if audio_filter:
        args.extend(["-af", audio_filter])

    # If not using RFC pan filter, use -ac 2 for built-in downmix
    if not cfg.use_rfc7845_downmix:
        args.extend(["-ac", str(cfg.audio_channels)])

    args.extend([
        "-c:a", cfg.audio_encoder,
        "-b:a", cfg.audio_bitrate,
    ])
    return args
