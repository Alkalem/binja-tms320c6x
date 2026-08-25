import logging

from binaryninja.log import log_alert, log_debug, log_error, log_info, log_warn


MODULE_NAME = 'TMS320C6x'

class DefaultFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(fmt='%(message)s')

    def format(self, record: logging.LogRecord) -> str:
        prefix = ''
        if hasattr(record, 'addr'):
            prefix += '@{record.addr:08x}: '
        return prefix + super().format(record)

class LogHandler(logging.Handler):
    def __init__(self) -> None:
        '''Initialize with default formatter and DEBUG log level.'''
        super().__init__(level=logging.DEBUG)
        self.formatter = DefaultFormatter()

    def _format_logger(self, name: str) -> str:
        parts = name.split('.')[:2]
        parts[0] = MODULE_NAME
        return '.'.join(map(lambda s: s.capitalize(), parts))

    def emit(self, record: logging.LogRecord):
        '''
        Emit a record to Binary Ninja.

        If a formatter is specified, it is used to format the record.
        The record is then written to Binary Ninjas log of the appropriate level.
        '''
        try:
            msg = self.format(record)
            logger = self._format_logger(record.name)
            match record.levelno:
                case logging.CRITICAL: log_alert(msg, logger)
                case logging.ERROR: log_error(msg, logger)
                case logging.WARNING: log_warn(msg, logger)
                case logging.INFO: log_info(msg, logger)
                case logging.DEBUG: log_debug(msg, logger)
        except Exception:
            self.handleError(record)

def init():
    '''Initialize logging, forwarding all log messages to Binary Ninja.'''
    plugin_root_logger = logging.getLogger(__name__.split('.')[0])
    plugin_root_logger.setLevel(logging.DEBUG)
    plugin_root_logger.addHandler(LogHandler())
    plugin_root_logger.propagate = False
