from .base import MacroBaseNode


class WaitNode(MacroBaseNode):
    """Pauses the macro for a fixed duration before continuing."""

    __identifier__ = 'macro'
    NODE_NAME = 'Wait'

    def __init__(self):
        super(WaitNode, self).__init__()
        self.add_input('in', multi_input=True)
        self.add_output('out')
        self.add_text_input('duration_ms', 'Duration (ms)', text='1000')
