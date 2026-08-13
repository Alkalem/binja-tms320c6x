import logging

from binaryninja.log import log_error

MODULE_NAME = 'TMS320C6x'

class Logger:
    @staticmethod
    def log_assert(cond, msg, addr: int = -1):
        if addr >= 0:
            msg = f'@{addr:08x}: {msg}'
        if not cond:
            log_error(msg, MODULE_NAME)
