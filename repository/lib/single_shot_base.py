from ndscan.experiment import ExpFragment, ResultChannel

class SingleShotBase(ExpFragment):
    """One physical attempt. Subclasses must expose their output channels."""

    def get_counts_handle(self) -> ResultChannel:
        """Return the integer counts channel handle."""
        raise NotImplementedError