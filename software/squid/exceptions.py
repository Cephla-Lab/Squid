class SquidError(RuntimeError):
    pass


class SquidTimeout(SquidError, TimeoutError):
    pass


class SquidReplyTimeout(SquidTimeout):
    """No readable reply came from a device within its timeout: the reply was lost or could not be parsed. Unlike a
    plain SquidTimeout (e.g. a stage still busy after wait_for_idle), the device may be fine and the command can be
    sent again."""

    pass
