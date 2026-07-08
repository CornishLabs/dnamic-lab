from artiq.experiment import *

class InteractiveArgsExample(EnvExperiment):
    """
    With standard arguments, it is only possible to use setattr_argument() 
    in build(); these arguments are always requested at submission time. 
    However, it is also possible to use interactive arguments, which can be 
    requested and supplied inside run(), while the experiment is being executed.
     
    Close and reopen the submission window, or click on the button labeled
    'Recompute all arguments', in order to update the submission parameters. 
    Submit again. It should print once, then wait; you may notice in
    'Schedule' that the experiment does not exit, but hangs at
    status 'running'.

    Now, in the same dock as 'Explorer', navigate to the tab 'Interactive Args'.
    You can now choose and submit a value for 'repeat'. Every time an 
    interactive argument is requested, the experiment pauses until an input 
    is supplied. 
    
    Note

    If you choose to 'Cancel' instead, an CancelledArgsError will be raised 
    (which an experiment can catch, instead of halting).
    
    In order to request and supply multiple interactive arguments at once, 
    simply place them in the same with block.
    """
    def build(self):
        pass

    def run(self):
        repeat = True
        while repeat:
            print("Hello World")
            with self.interactive(title="Repeat?") as interactive:
                interactive.setattr_argument("repeat", BooleanValue(True))
            repeat = interactive.repeat
