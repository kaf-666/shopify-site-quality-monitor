"""Screenshot artifact lifecycle management."""

__all__ = (
    "ScreenshotArtifactManager",
    "finalize_artifact_run",
    "safe_move",
)


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    from playwright_checks.artifacts import screenshot_manager

    return getattr(screenshot_manager, name)
