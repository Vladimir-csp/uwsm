import os
import sys
import re
import textwrap
import random
import syslog
import traceback
from io import StringIO
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
    "Holds global state of syslog logging and loglevel prefix switches"
    # log using syslog module
    log = False
    # prefix lines with <N> codes for stdin/stderr journal parsing
    prefix = False


class Styles:
    "Terminal control characters for color and style"
    reset = "\033[0m"
    red = "\033[31m"
    green = "\033[32m"
    yellow = "\033[33m"
    pale_yellow = "\033[97m"
    blue = "\033[34m"
    violet = "\033[35m"
    grey = "\033[90m"
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


def print_fancy(*what, **how):
    """
    Prints to 'file' (sys.stdout) with flush.
    In 'color' (Styles.green) if 'file' is a tty.
    'notify': 0: no, 1: if 'file' is not a tty, 2: always
    'notify_urgency': 0
    'log': False (also log to syslog)
    'loglevel': 0-7 (EMERG-DEBUG), default 5 (NOTICE)
    'logprefix': False (prefix lines with 'loglevel' for journal)
    """
    file = how.pop("file", sys.stdout)
    color = how.pop("color", Styles.green)
    notify = how.pop("notify", 0)
    notify_urgency = how.pop("notify_urgency", 0)
    log = how.pop("log", LogFlag.log)
    loglevel = how.pop("loglevel", 5)
    logprefix = how.pop("logprefix", LogFlag.prefix)

    # print colored text for interactive output
    if file.isatty():
        print(color, end="", file=file, flush=True)
        print(*what, **how, file=file, flush=True)
        print(Styles.reset, end="", file=file, flush=True)
    # print lines prefixed with loglevel for journal
    elif logprefix:
        # print to fake file, add line prefixes, print for real
        print_string = StringIO()
        print(*what, **how, file=print_string, flush=True)
        prefixed_lines = []
        for line in print_string.getvalue().splitlines():
            prefixed_lines.append(f"<{loglevel}>{line}")
        print("\n".join(prefixed_lines), **how, file=file, flush=True)
    # simple print
    else:
        print(*what, **how, file=file, flush=True)

    if log:
        sl_level = [
            syslog.LOG_EMERG,
            syslog.LOG_ALERT,
            syslog.LOG_CRIT,
            syslog.LOG_ERR,
            syslog.LOG_WARNING,
            syslog.LOG_NOTICE,
            syslog.LOG_INFO,
            syslog.LOG_DEBUG,
        ][loglevel]
        syslog.syslog(sl_level | syslog.LOG_USER, str(*what))

    if notify and (not file.isatty() or notify == 2):
        try:
            bus_session = DbusInteractions("session")
            msg = str(*what)
            bus_session.notify(summary="Message", body=msg, urgency=notify_urgency)
        except Exception as caught_exception:
            print_warning(caught_exception, notify=0)


def print_ok(*what, **how):
    """
    Prints to 'file' (sys.stdout) with flush.
    In 'color' (green) if 'file' is a tty.
    'notify': 0: no, 1: if 'file' is not a tty, 2: always
    'notify_urgency': 0
    'log': False (also log to syslog)
    'loglevel': 0-7 (EMERG-DEBUG), default 5 (NOTICE)
    'logprefix': False (prefix lines with 'loglevel' for journal)
    """
    file = how.pop("file", sys.stdout)
    color = how.pop("color", Styles.green)
    notify = how.pop("notify", 0)
    notify_urgency = how.pop("notify_urgency", 0)
    log = how.pop("log", LogFlag.log)
    loglevel = how.pop("loglevel", 5)
    logprefix = how.pop("logprefix", LogFlag.prefix)

    print_fancy(
        *what,
        **how,
        file=file,
        color=color,
        notify=notify,
        notify_urgency=notify_urgency,
        log=log,
        loglevel=loglevel,
        logprefix=logprefix,
    )


def print_warning(*what, **how):
    """
    Prints to 'file' (sys.stdout) with flush.
    In 'color' (Styles.yellow) if 'file' is a tty.
    'notify': 0: no, 1: if 'file' is not a tty, 2: always
    'notify_urgency': 1
    'log': False (also log to syslog)
    'loglevel': 0-7 (EMERG-DEBUG), default 4 (WARNING)
    'logprefix': False (prefix lines with 'loglevel' for journal)
    """
    file = how.pop("file", sys.stdout)
    color = how.pop("color", Styles.yellow)
    notify = how.pop("notify", 0)
    notify_urgency = how.pop("notify_urgency", 1)
    log = how.pop("log", LogFlag.log)
    loglevel = how.pop("loglevel", 4)
    logprefix = how.pop("logprefix", LogFlag.prefix)

    print_fancy(
        *what,
        **how,
        file=file,
        color=color,
        notify=notify,
        notify_urgency=notify_urgency,
        log=log,
        loglevel=loglevel,
        logprefix=logprefix,
    )


def print_error(*what, **how):
    """
    Prints to 'file' (sys.stderr) with flush.
    In 'color' (Styles.red) if 'file' is a tty.
    'notify': 0: no, 1: if 'file' is not a tty, 2: always
    'notify_urgency': 2
    'log': False (also log to syslog)
    'loglevel': 0-7 (EMERG-DEBUG), default 3 (ERR)
    'logprefix': False (prefix lines with 'loglevel' for journal)
    """
    file = how.pop("file", sys.stderr)
    color = how.pop("color", Styles.red)
    notify = how.pop("notify", 0)
    notify_urgency = how.pop("notify_urgency", 2)
    log = how.pop("log", LogFlag.log)
    loglevel = how.pop("loglevel", 3)
    logprefix = how.pop("logprefix", LogFlag.prefix)

    print_fancy(
        *what,
        **how,
        file=file,
        color=color,
        notify=notify,
        notify_urgency=notify_urgency,
        log=log,
        loglevel=loglevel,
        logprefix=logprefix,
    )


if DebugFlag.debug:
    from inspect import stack

    def print_debug(*what, **how):
        "Prints to stderr with DEBUG and END_DEBUG marks"
        dsep = "\n" if "sep" not in how or "\n" not in how["sep"] else ""
        file = how.pop("file", sys.stderr)
        color = how.pop("color", Styles.grey)
        notify = 0
        notify_urgency = 0
        log = how.pop("log", LogFlag.log)
        loglevel = 7
        logprefix = how.pop("logprefix", LogFlag.prefix)

        my_stack = stack()
        print_fancy(
            f"DEBUG {my_stack[1].filename}:{my_stack[1].lineno} {my_stack[1].function}{dsep}",
            *what,
            f"{dsep}END_DEBUG",
            **how,
            file=file,
            color=color,
            notify=notify,
            notify_urgency=notify_urgency,
            log=log,
            loglevel=loglevel,
            logprefix=logprefix,
        )

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
