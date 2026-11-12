"""Whether this process is a demo server, cached (building `Settings` takes about 60 ms, too
slow for a check made on every model call or e-mail).

Code that already holds a `Settings` object reads `settings.demo_mode` directly; code that
does not calls `is_demo()`. Tests that need demo mode patch `mode.is_demo`.
"""

import functools


@functools.lru_cache(maxsize=1)
def _enabled() -> bool:
    from callobuzz_rag.core.config import get_settings

    return bool(get_settings().demo_mode)


def is_demo() -> bool:
    return _enabled()


def reset_cache() -> None:
    """Forget the cached answer (tests that change the environment)."""
    _enabled.cache_clear()
