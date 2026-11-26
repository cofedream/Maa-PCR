"""Typed reroll outcome: the request may or may not have reached the server.

Only the labyrinth reroll (ENTER/RETIRE) uses this. It never means "the game
state is unknown for a battle or reward"; those still stop without resending.
"""


class RerollUnknown(RuntimeError):
    def __init__(self, reason):
        if type(reason) is not str or not reason:
            raise ValueError('reroll_unknown_reason_required')
        super().__init__(reason)
