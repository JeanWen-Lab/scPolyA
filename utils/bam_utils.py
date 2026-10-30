
from pathlib import Path
import subprocess
import logging
from logging_config import get_logger, LoggersWrapper, LOG_MAIN, LOG_UTILS, suffix_scope

suffix = lambda x: f" (chromosome_utils:{x})"
logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)





