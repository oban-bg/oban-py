import random
from typing import Literal


def exponential(
    attempt: int, *, max_pow: int = 10, min_pad: int = 0, mult: int = 1
) -> int:
    """Calculate exponential backoff delay in seconds.

    Args:
        attempt: The retry attempt number
        max_pow: Maximum power of 2 (default: 10)
        min_pad: Minimum padding in seconds (default: 0)
        mult: Multiplier for the exponential value (default: 1)

    Returns:
        Backoff delay in seconds
    """
    return min_pad + mult * pow(2, min(attempt, max_pow))


def jitter(
    time: int, *, mode: Literal["inc", "dec", "both"] = "both", mult: float = 0.1
) -> int:
    """Add jitter to a backoff time.

    Args:
        time: Base time in seconds
        mode: Jitter mode - "inc" (increase only), "dec" (decrease only), or "both" (default: "both")
        mult: Jitter multiplier (default: 0.1)

    Returns:
        Time with jitter applied
    """
    rand = random.random()
    diff = int(rand * mult * time)

    match mode:
        case "inc":
            return time + diff
        case "dec":
            return time - diff
        case "both":
            return time + diff if rand >= 0.5 else time - diff


def jittery_clamped(attempt: int, max_attempts: int, *, clamped_max: int = 20) -> int:
    """Calculate jittery clamped backoff.

    Args:
        attempt: The retry attempt number
        max_attempts: Maximum attempts allowed
        clamped_max: Maximum value to clamp attempts to (default: 20)

    Returns:
        Backoff delay in seconds with exponential backoff and jitter
    """
    if max_attempts <= clamped_max:
        clamped_attempt = attempt
    else:
        clamped_attempt = round(attempt / max_attempts * clamped_max)

    time = exponential(clamped_attempt, mult=1, max_pow=100, min_pad=15)

    return jitter(time, mode="inc")
