"""Root conftest — registers Hypothesis profiles for the project."""

import platform

from hypothesis import HealthCheck, settings

_on_windows = platform.system() == "Windows"

# Default profile: relax deadline and suppress too_slow only on Windows (NTFS + DB-heavy tests)
settings.register_profile(
    "default",
    max_examples=100,
    deadline=None if _on_windows else 500,
    suppress_health_check=[HealthCheck.too_slow] if _on_windows else [],
)

settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile("default")
