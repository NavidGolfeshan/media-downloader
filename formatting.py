"""Human-readable sizes, speeds and times (shared by the terminal interface and jobs)."""


def format_size(num_bytes: float | None) -> str:
    if num_bytes is None or num_bytes < 0:
        return "?"
    if num_bytes >= 1e9:
        return f"{num_bytes / 1e9:.2f} GB"
    if num_bytes >= 1e6:
        return f"{num_bytes / 1e6:.1f} MB"
    if num_bytes >= 1e3:
        return f"{num_bytes / 1e3:.0f} KB"
    return f"{int(num_bytes)} B"


def format_speed(bytes_per_second: float | None) -> str:
    if not bytes_per_second or bytes_per_second < 0:
        return "-- /s"
    return f"{format_size(bytes_per_second)}/s"


def format_duration(seconds: float | None) -> str:
    """Length of a video/track: 3:45 or 1:02:05 ('' when unknown)."""
    if not seconds or seconds < 0:
        return ""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
