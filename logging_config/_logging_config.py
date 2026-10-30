# scPolyA_peak_calling/_logging_config.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Type, TypeVar, Callable

from tqdm import tqdm
import contextvars
from contextlib import contextmanager

CURRENT_STAGE = contextvars.ContextVar("current_stage", default=None)
CURRENT_TASK = contextvars.ContextVar("current_task", default=None)
CURRENT_SUFFIX = contextvars.ContextVar("current_suffix", default=None)
CURRENT_INVOCATION_ID = contextvars.ContextVar("current_invocation_id", default=None)
CURRENT_IDENTITY = contextvars.ContextVar("current_identity", default=None)

C_PINK = "\033[95m"
C_GREEN = "\033[92m"
C_RESET = "\033[0m"
C_RED = "\033[91m"

def color_msg(msg: str, color_code: str) -> str:
    return f"{color_code}{msg}{C_RESET}"

log_prefix: Callable[[str], str] = lambda x: f"[{x}] "

TExc = TypeVar("TExc", bound=BaseException)

@contextmanager
def stage_scope(_stage: str):
    token = CURRENT_STAGE.set(_stage)
    try:
        yield
    finally:
        CURRENT_STAGE.reset(token)

@contextmanager
def task_scope(_task: str):
    token = CURRENT_TASK.set(_task)
    try:
        yield
    finally:
        CURRENT_TASK.reset(token)

@contextmanager
def suffix_scope(_suffix: str):
    token = CURRENT_SUFFIX.set(_suffix)
    try:
        yield
    finally:
        CURRENT_SUFFIX.reset(token)


@contextmanager
def invocation_scope(invocation_id: str):
    token = CURRENT_INVOCATION_ID.set(str(invocation_id))
    try:
        yield
    finally:
        CURRENT_INVOCATION_ID.reset(token)


@contextmanager
def identity_scope(identity: str):
    token = CURRENT_IDENTITY.set(str(identity))
    try:
        yield
    finally:
        CURRENT_IDENTITY.reset(token)


class ScPolyALogger(logging.Logger):
    """
    Project logger with ContextVars support, typed `raise_error()`, and auto-formatting.
    """

    def _fmt(self, msg: str, task: str | None = None, origin: str | None = None,
             root_color: str | None = None, task_color: str | None = None) -> str:
        root = CURRENT_STAGE.get() or origin or self.name
        if root_color:
            root = f"{root_color}{root}{C_RESET}"
        final_task = CURRENT_TASK.get() or task
        if task_color:
            final_task = f"{task_color}{final_task}{C_RESET}"
        suffix = CURRENT_SUFFIX.get() or ""
        return f"[{':'.join([p for p in [root, final_task] if p])}] {msg}{suffix}"

    def _context_kwargs(
        self,
        kwargs: dict,
        *,
        task: str | None,
        origin: str | None,
        channel: str | None,
    ) -> dict:
        extra = dict(kwargs.pop("extra", {}) or {})
        extra.setdefault("scpolya_invocation_id", CURRENT_INVOCATION_ID.get() or "")
        extra.setdefault("scpolya_stage", CURRENT_STAGE.get() or origin or "")
        extra.setdefault("scpolya_task", CURRENT_TASK.get() or task or "")
        extra.setdefault("scpolya_identity", CURRENT_IDENTITY.get() or "")
        extra.setdefault("scpolya_channel", channel or "")
        kwargs["extra"] = extra
        return kwargs

    def debug(self, msg: object, *args, task: str | None = None, origin: str | None = None,
              root_color=None, task_color=None, channel: str | None = None, **kwargs) -> None:
        if isinstance(msg, str):
            msg = self._fmt(msg, task=task, origin=origin, root_color=root_color, task_color=task_color)
        kwargs = self._context_kwargs(kwargs, task=task, origin=origin, channel=channel)
        super().debug(msg, *args, **kwargs)

    def info(self, msg: object, *args, task: str | None = None, origin: str | None = None,
             root_color=None, task_color=None, channel: str | None = None, **kwargs) -> None:
        if isinstance(msg, str):
            msg = self._fmt(msg, task=task, origin=origin, root_color=root_color, task_color=task_color)
        kwargs = self._context_kwargs(kwargs, task=task, origin=origin, channel=channel)
        super().info(msg, *args, **kwargs)

    def warning(self, msg: object, *args, task: str | None = None, origin: str | None = None,
                root_color=None, task_color=None, channel: str | None = None, **kwargs) -> None:
        if isinstance(msg, str):
            msg = self._fmt(msg, task=task, origin=origin, root_color=root_color, task_color=task_color)
        kwargs = self._context_kwargs(kwargs, task=task, origin=origin, channel=channel)
        super().warning(msg, *args, **kwargs)

    def error(self, msg: object, *args, task: str | None = None, origin: str | None = None,
              root_color=None, task_color=None, channel: str | None = None, **kwargs) -> None:
        if isinstance(msg, str):
            msg = self._fmt(msg, task=task, origin=origin, root_color=root_color, task_color=task_color)
        kwargs = self._context_kwargs(kwargs, task=task, origin=origin, channel=channel)
        super().error(msg, *args, **kwargs)

    def critical(self, msg: object, *args, task: str | None = None, origin: str | None = None,
                 root_color=None, task_color=None, channel: str | None = None, **kwargs) -> None:
        if isinstance(msg, str):
            msg = self._fmt(msg, task=task, origin=origin, root_color=root_color, task_color=task_color)
        kwargs = self._context_kwargs(kwargs, task=task, origin=origin, channel=channel)
        super().critical(msg, *args, **kwargs)

    def raise_error(self, message: str,
                    exception_cls: Type[TExc] = Exception,
                    task: str | None = None,
                    origin: str | None = None,
                    root_color=None, task_color=None,
                    channel: str | None = None) -> "NoReturn":  # type: ignore[name-defined]

        self.error(
            message,
            task=task,
            origin=origin,
            root_color=root_color,
            task_color=task_color,
            channel=channel,
        )
        raise exception_cls(message)


logging.setLoggerClass(ScPolyALogger)


@dataclass(frozen=True)
class LoggersWrapper:
    """
    Simpler wrapper. No longer needs to manage 'module' or 'origin' manually,
    as ScPolyALogger handles context resolution.
    """
    main: ScPolyALogger
    sub: ScPolyALogger

    def info(self, msg: str, task: str | None = None, origin: str | None = None,
             root_color: str | None = None, task_color: str | None = None) -> None:
        self.main.info(
            msg,
            task=task,
            origin=origin,
            root_color=root_color,
            task_color=task_color,
            channel=self.sub.name if _runtime_fanout_active() else None,
        )

    def warning(self, msg: str, task: str | None = None, origin: str | None = None,
             root_color: str | None = None, task_color: str | None = None) -> None:
        if not _runtime_fanout_active():
            self.sub.warning(msg, task=task, origin=origin)
        self.main.warning(
            msg,
            task=task,
            origin=origin,
            root_color=root_color,
            task_color=task_color,
            channel=self.sub.name if _runtime_fanout_active() else None,
        )

    def debug(self, msg: str, task: str | None = None, origin: str | None = None,
             root_color: str | None = None, task_color: str | None = None) -> None:
        if not _runtime_fanout_active():
            self.sub.debug(msg, task=task, origin=origin)
        self.main.debug(
            msg,
            task=task,
            origin=origin,
            root_color=root_color,
            task_color=task_color,
            channel=self.sub.name if _runtime_fanout_active() else None,
        )

    def raise_error(self, msg: str, e: Type[TExc] = Exception, task: str | None = None, origin: str | None = None,
             root_color: str | None = None, task_color: str | None = None) -> None:
        if not _runtime_fanout_active():
            self.sub.error(msg, task=task, origin=origin)
        self.main.raise_error(
            msg,
            e,
            task=task,
            origin=origin,
            root_color=root_color,
            task_color=task_color,
            channel=self.sub.name if _runtime_fanout_active() else None,
        )


def _runtime_fanout_active() -> bool:
    try:
        from logging_config.runtime_session import get_active_logging_session

        return get_active_logging_session() is not None
    except ImportError:
        return False


def get_logger(name: str) -> ScPolyALogger:
    """
    Return a logger that is typed as `ScPolyALogger`.

    If the logger was created before `setLoggerClass()` ran (rare, but can happen
    in some import orders), we "upgrade" it by replacing the instance in the
    logging manager.
    """
    logger = logging.getLogger(name)

    if isinstance(logger, ScPolyALogger):
        return logger

    # Upgrade an already-created standard Logger into our subclass instance.
    upgraded = ScPolyALogger(name)
    upgraded.__dict__.update(logger.__dict__)  # preserve handlers/level/filters, etc.
    logging.Logger.manager.loggerDict[name] = upgraded
    return upgraded


class TqdmLoggingHandler(logging.StreamHandler):
    def emit(self, record) -> None:
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)


class MultiLineFormatter(logging.Formatter):
    def format(self, record) -> str:
        message = super().format(record)
        message = message.replace(C_RESET, "").replace(C_PINK, "").replace(C_GREEN, "").replace(C_RED, "")

        lines = message.split("\n")
        if len(lines) > 1:
            head, tail = lines[0].split(" | ", 1)
            indent = " " * (len(head) + 3)
            rest = "\n".join(indent + line for line in lines[1:])
            return lines[0] + "\n" + rest
        return message


class MultiLineConsoleFormatter(logging.Formatter):
    def format(self, record) -> str:
        message = super().format(record)
        lines = message.split("\n")

        if len(lines) > 1:
            return lines[0] + "\n" + " " * 8 + ("\n" + " " * 8).join(lines[1:])
        return message


def setup_main_logger(log_dir: Path, verbose: bool = True) -> ScPolyALogger:
    from logging_config.define import LOG_MAIN
    logger = get_logger(LOG_MAIN)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    formatter = MultiLineFormatter(
        "%(asctime)s %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = MultiLineConsoleFormatter(
        "\033[36m%(asctime)s\033[0m \033[33m%(levelname)s\033[0m | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "main.log"

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = TqdmLoggingHandler()
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logo = r"""
====███████╗=██████╗██████╗==██████╗=██╗==██╗===██╗=█████╗=======
====██╔════╝██╔════╝██╔══██╗██╔═══██╗██║==╚██╗=██╔╝██╔══██╗======
====███████╗██║=====██████╔╝██║===██║██║===╚████╔╝=███████║======
====╚════██║██║=====██╔═══╝=██║===██║██║====╚██╔╝==██╔══██║======
====███████║╚██████╗██║=====╚██████╔╝███████╗██║===██║==██║======
====╚══════╝=╚═════╝╚═╝======╚═════╝=╚══════╝╚═╝===╚═╝==╚═╝======
=================================================================
"""
    logger.info(logo)
    return logger


def setup_module_logger(
        name: str,
        log_dir: Path,
        console_output: bool = True,
        main_logger: Optional[logging.Logger] = None,
        verbose: bool = True,
) -> ScPolyALogger:
    try:
        from logging_config.runtime_session import get_active_logging_session

        session = get_active_logging_session()
    except ImportError:
        session = None
    if session is not None:
        return session.configure_module_logger(
            name,
            console_output=console_output,
        )

    logger = get_logger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    formatter = MultiLineFormatter(
        "%(asctime)s - %(name)s - %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = MultiLineConsoleFormatter(
        "\033[33m%(asctime)s - %(name)s\033[0m - %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{name}.log"

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if console_output:
        console_handler = TqdmLoggingHandler()
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    logger.propagate = False

    if main_logger:
        main_logger.info(f"Set up logger {name}\nconsole output: {console_output}; log file: {log_file}")

    return logger
