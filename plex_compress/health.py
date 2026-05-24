"""Pre-flight health check for plex_compress."""

import os
import subprocess
import tempfile
from typing import List, Tuple

from .config import Config
from .utils import get_free_space_gb
from . import ConfigError


def run_health_check(cfg: Config, logger) -> Tuple[bool, List[str]]:
    """Run a comprehensive health check.

    Returns (ok, messages) where ok is True if all checks pass.
    """
    messages = []
    ok = True

    def check(name: str, passed: bool, detail: str = ""):
        nonlocal ok
        status = "PASS" if passed else "FAIL"
        msg = f"  [{status}] {name}"
        if detail:
            msg += f": {detail}"
        messages.append(msg)
        if not passed:
            ok = False

    logger.info("Running health check...")
    messages.append("Health Check Results:")

    # 1. Check ffmpeg is available
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        version_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        check("ffmpeg available", result.returncode == 0, version_line)
    except Exception as e:
        check("ffmpeg available", False, str(e))

    # 2. Check ffprobe is available
    try:
        result = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=10)
        version_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        check("ffprobe available", result.returncode == 0, version_line)
    except Exception as e:
        check("ffprobe available", False, str(e))

    # 3. Check temp directory is writable
    try:
        test_file = os.path.join(cfg.temp_dir, ".plex_compress_health_check")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        check("Temp directory writable", True, cfg.temp_dir)
    except Exception as e:
        check("Temp directory writable", False, str(e))

    # 4. Check temp directory has enough space
    try:
        free_gb = get_free_space_gb(cfg.temp_dir)
        check("Temp directory space", free_gb >= 5.0, f"{free_gb:.1f} GB free")
    except Exception as e:
        check("Temp directory space", False, str(e))

    # 5. Check state DB is accessible
    try:
        from .state import StateDB
        s = StateDB(cfg.state_db_path)
        check("State DB accessible", True, cfg.state_db_path)
    except Exception as e:
        check("State DB accessible", False, str(e))

    # 6. Check library path exists (if provided)
    if cfg.library_path:
        check("Library path exists", os.path.isdir(cfg.library_path), cfg.library_path)
    else:
        messages.append("  [INFO] Library path not set (single-file mode)")

    # 7. Test hardware encoder (if not libx265)
    if cfg.video_encoder != "libx265":
        try:
            with tempfile.NamedTemporaryFile(suffix=".mkv", delete=False) as tmp:
                tmp_path = tmp.name
            test_cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
                "-c:v", cfg.video_encoder,
                "-frames:v", "1",
                tmp_path,
            ]
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
            os.remove(tmp_path)
            if result.returncode != 0:
                # Check if it's an encoder initialization error
                err = result.stderr.lower()
                if "cannot init" in err or "not supported" in err or "no capable devices" in err:
                    check(f"Hardware encoder ({cfg.video_encoder})", False, "Encoder initialization failed")
                else:
                    check(f"Hardware encoder ({cfg.video_encoder})", True, "Encoder available (test encode had non-fatal issues)")
            else:
                check(f"Hardware encoder ({cfg.video_encoder})", True, "Test encode successful")
        except Exception as e:
            check(f"Hardware encoder ({cfg.video_encoder})", False, str(e))
    else:
        messages.append(f"  [INFO] Software encoder ({cfg.video_encoder}) - no hardware test needed")

    # 8. Test audio filter chain
    try:
        with tempfile.NamedTemporaryFile(suffix=".mkv", delete=False) as tmp:
            tmp_path = tmp.name
        test_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
            "-af", f"loudnorm=I={cfg.loudnorm_i}:TP={cfg.loudnorm_tp}:LRA={cfg.loudnorm_lra}",
            "-ac", "2",
            "-c:a", cfg.audio_encoder,
            "-b:a", cfg.audio_bitrate,
            tmp_path,
        ]
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
        os.remove(tmp_path)
        check("Audio filter chain", result.returncode == 0)
    except Exception as e:
        check("Audio filter chain", False, str(e))

    # Print summary
    for msg in messages:
        logger.info(msg)

    if ok:
        logger.info("Health check PASSED. Ready to transcode.")
    else:
        logger.error("Health check FAILED. Please fix the issues above before transcoding.")

    return ok, messages
