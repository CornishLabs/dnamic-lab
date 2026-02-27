import importlib
import logging
import sys

logger = logging.getLogger(__name__)

# class MultilineFormatter(logging.Formatter):
#     def __init__(self):
#         logging.Formatter.__init__(
#             self, "%(levelname)s:%(name)s:%(message)s")

#     def format(self, record):
#         r = logging.Formatter.format(self, record)
#         linebreaks = r.count("\n")
#         if linebreaks:
#             i = r.index(":")
#             r = r[:i] + "<" + str(linebreaks + 1) + ">" + r[i:]
#         return r
    
# handler = logging.StreamHandler()
# handler.setFormatter(MultilineFormatter())
# logger.addHandler(handler)