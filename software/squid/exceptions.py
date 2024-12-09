class SquidError(RuntimeError):
    pass

class SquidTimeout(SquidError, TimeoutError):
    pass
