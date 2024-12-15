import os
import sys
import re
import textwrap
import random
import syslog
import traceback
from typing import List


class DebugFlag:
    "Checks for DEBUG env value and holds 'debug' boolean, 'warning' string"
    debug = os.getenv("DEBUG", "0")
    warning = None
    if debug.isnumeric():
        debug = int(debug) > 0
    elif not debug:
        debug = False
    elif debug.lower().capitalize() in ("Yes", "True", "Y"):
        debug = True
    elif not debug or debug.lower().capitalize() in ("No", "False", "N"):
        debug = False
    else:
        warning = f'Expected boolean or numeric or empty value for DEBUG, got "{debug}", assuming False'
        debug = False


class LogFlag:
    "Holds global state of syslog logging and string prefix"
    log = False


class Styles:
    "Terminal control characters for color and style"
    reset = "\033[0m"
    red = "\033[31m"
    green = "\033[32m"
    yellow = "\033[33m"
    pale_yellow = "\033[97m"
    blue = "\033[34m"
    violet = "\033[35m"
    header = "\033[95m"
    bold = "\033[1m"
    under = "\033[4m"
    strike = "\033[9m"
    flash = "\033[5m"


class Val:
    "Compiled re patterns for validation"
    wm_id = re.compile(r"\A[a-zA-Z0-9_:.-]+\Z", re.MULTILINE)
    dn_colon = re.compile(r"\A[a-zA-Z0-9_.-]+(:[a-zA-Z0-9_.-]+)*\Z", re.MULTILINE)
    entry_id = re.compile(r"\A[a-zA-Z0-9_][a-zA-Z0-9_.-]*.desktop\Z", re.MULTILINE)
    action_id = re.compile(r"\A[a-zA-Z0-9-]+\Z", re.MULTILINE)
    unit_ext = re.compile(
        r"[a-zA-Z0-9_:.\\-]+@?\.(service|slice|scope|target|socket|d/[a-zA-Z0-9_:.\\-]+.conf)\Z",
        re.MULTILINE,
    )
    sh_varname = re.compile(
        r"\A([a-zA-Z_][a-zA-Z0-9_]+|[a-zA-Z][a-zA-Z0-9_]*)\Z", re.MULTILINE
    )
    invalid_locale_key_error = re.compile(r"^Invalid key: \w+\[.+\]$")


def dedent(data: str) -> str:
    "Applies dedent, lstrips newlines, rstrips except single newline"
    data = textwrap.dedent(data).lstrip("\n")
    return data.rstrip() + "\n" if data.endswith("\n") else data.rstrip()


def random_hex(length: int = 16) -> str:
    "Returns random hex string of length"
    return "".join([random.choice(list("0123456789abcdef")) for _ in range(length)])


def sane_split(string: str, delimiter: str) -> List[str]:
    "Splits string by delimiter, but returns empty list on empty string"
    if not isinstance(string, str):
        raise TypeError(f'"string" should be a string, got: {type(string)}')
    if not isinstance(delimiter, str):
        raise TypeError(f'"delimiter" should be a string, got: {type(delimiter)}')
    if not delimiter:
        raise ValueError('"delimiter" should not be empty')
    return string.split(delimiter) if string else []


# all print_* functions force flush for synchronized output
def print_normal(*what, **how):
    """
    Normal print with flush.
    optional 'log': False
    """
    log = how.pop("log", LogFlag.log)

    print(*what, **how, flush=True)

    if log:
        syslog.syslog(syslog.LOG_INFO | syslog.LOG_USER, str(*what))


def print_ok(*what, **how):
    """
    Prints to stdout ('file') with flush.
    In green if 'file' is a tty.
    'notify': 0: no, 1: if 'file' is not a tty, 2: always
    'notify_urgency': 0
    'log': False
    """
    file = how.pop("file", sys.stdout)
    notify = how.pop("notify", 0)
    notify_urgency = how.pop("notify_urgency", 0)
    log = how.pop("log", LogFlag.log)

    if file.isatty():
        print(Styles.green, end="", file=file, flush=True)
    print(*what, **how, file=file, flush=True)
    if file.isatty():
        print(Styles.reset, end="", file=file, flush=True)

    if log:
        syslog.syslog(syslog.LOG_NOTICE | syslog.LOG_USER, str(*what))

    if notify and (not file.isatty() or notify == 2):
        try:
            bus_session = DbusInteractions("session")
            msg = str(*what)
            bus_session.notify(summary="Message", body=msg, urgency=notify_urgency)
        except Exception as caught_exception:
            print_warning(caught_exception, notify=0)


def print_warning(*what, **how):
    """
    Prints to stdout ('file') with flush.
    In yellow if 'file' is a tty.
    'notify': 0: no, 1: if 'file' is a tty, 2: always
    'notify_urgency': 1
    'log': False
    """
    file = how.pop("file", sys.stdout)
    notify = how.pop("notify", 0)
    notify_urgency = how.pop("notify_urgency", 1)
    log = how.pop("log", LogFlag.log)

    if file.isatty():
        print(Styles.yellow, end="", file=file, flush=True)
    print(*what, **how, file=file, flush=True)
    if file.isatty():
        print(Styles.reset, end="", file=file, flush=True)

    # in debug mode find and print exceptions to stderr
    if DebugFlag.debug:
        for item in what:
            if isinstance(item, Exception):
                traceback.print_exception(item, file=sys.stderr)

    if log:
        syslog.syslog(syslog.LOG_WARNING | syslog.LOG_USER, str(*what))

    if notify and (not file.isatty() or notify == 2):
        try:
            bus_session = DbusInteractions("session")
            msg = str(*what)
            bus_session.notify(
                summary="Warning", body=msg, app_icon="warning", urgency=notify_urgency
            )
        except Exception as caught_exception:
            print_warning(caught_exception, notify=0)


def print_error(*what, **how):
    """
    Prints to stderr ('file') with flush.
    In red if 'file' is a tty.
    'notify': 0: no, 1: if 'file' is a tty, 2: always
    'notify_urgency': 1
    'log': False
    """
    file = how.pop("file", sys.stderr)
    notify = how.pop("notify", 0)
    notify_urgency = how.pop("notify_urgency", 2)
    log = how.pop("log", LogFlag.log)

    if file.isatty():
        print(Styles.red, end="", file=file, flush=True)
    print(*what, **how, file=file, flush=True)
    if file.isatty():
        print(Styles.reset, end="", file=file, flush=True)

    # in debug mode find and print exceptions to stderr
    if DebugFlag.debug:
        for item in what:
            if isinstance(item, Exception):
                traceback.print_exception(item, file=sys.stderr)

    if log:
        syslog.syslog(syslog.LOG_ERROR | syslog.LOG_USER, str(*what))

    if notify and (not file.isatty() or notify == 2):
        try:
            bus_session = DbusInteractions("session")
            msg = str(*what)
            bus_session.notify(
                summary="Error", body=msg, app_icon="error", urgency=notify_urgency
            )
        except Exception as caught_exception:
            print_warning(caught_exception, notify=0)


if DebugFlag.debug:
    from inspect import stack

    def print_debug(*what, **how):
        "Prints to stderr with DEBUG and END_DEBUG marks"
        dsep = "\n" if "sep" not in how or "\n" not in how["sep"] else ""
        log = how.pop("log", LogFlag.log)

        my_stack = stack()
        print(
            f"DEBUG {my_stack[1].filename}:{my_stack[1].lineno} {my_stack[1].function}{dsep}",
            *what,
            f"{dsep}END_DEBUG",
            **how,
            file=sys.stderr,
            flush=True,
        )
        print(Styles.reset, end="", file=sys.stderr, flush=True)

        if log:
            syslog.syslog(syslog.LOG_DEBUG | syslog.LOG_USER, str(*what))

else:

    def print_debug(*what, **how):
        "Does nothing"
        pass


def print_style(stls, *what, **how):
    "Prints selected style(s), then args, then resets"
    if isinstance(stls, str):
        stls = [stls]
    for style in stls:
        print(style, end="", flush=True)
    print(*what, **how, flush=True)
    print(Styles.reset, end="", file=sys.stderr, flush=True)
