class Event:
    """Simple event stub."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


__all__ = ["Event"]
