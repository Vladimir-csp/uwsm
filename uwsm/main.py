"""
# Universal Wayland Desktop Session Manager
https://github.com/Vladimir-csp/uwsm
https://gitlab.freedesktop.org/Vladimir-csp/uwsm

Runs selected compositor with plugin-extendable tweaks
Manages systemd environment and targets along the way, providing a session
with XDG autostart support, application unit management, clean shutdown

Inspired by and uses some techniques from:
 https://github.com/xdbob/sway-services
 https://github.com/alebastr/sway-systemd
 https://github.com/swaywm/sway
 https://people.debian.org/~mpitt/systemd.conf-2016-graphical-session.pdf

Special thanks to @skewballfox for help with python and pointing me to useful tools.
"""

import os
import sys
import shlex
import argparse
import re
import subprocess
import textwrap
import random
import time
import signal
import traceback
import stat
from typing import List, Callable
from urllib import parse as urlparse

from xdg import BaseDirectory
from xdg.util import which
from xdg.DesktopEntry import DesktopEntry
from xdg.Exceptions import ValidationError

from uwsm.params import *
from uwsm.dbus import *


class CompGlobals:
    "Compositor global vars"
    # Full final compositor cmdline (list)
    cmdline: List[str] = []
    # Compositor arguments that were given on CLI
    cli_args: List[str] = []
    # Internal compositor ID (first of cli args)
    id: str = None
    # escaped string for unit specifier
    id_unit_string: str = None
    # processed function-friendly cmdline[0]
    bin_id: str = None
    # XDG_CURRENT_DESKTOP list
    desktop_names: List[str] = []
    # list of -D value
    cli_desktop_names: List[str] = []
    # -e flag bool
    cli_desktop_names_exclusive: bool = None
    # Final compositor Name
    name: str = None
    # value of -N
    cli_name: str = None
    # Final compositor Description
    description: str = None
    # value of -C
    cli_description: str = None


class Terminal:
    "XDG Terminal selector"
    entry: any = None
    entry_id: str = ""
    entry_action_id: str = ""
    neg_cache: dict = {}


class Stopper:
    "For use in signal trap"
    initiated: bool = False
    signals: list = []


class UnitsState:
    "Holds flag to mark changes in systemd units"
    changed: bool = False


class Varnames:
    "Sets of varnames"
    always_export = {
        "XDG_SESSION_ID",
        "XDG_SESSION_TYPE",
        "XDG_VTNR",
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_DESKTOP",
        "XDG_MENU_PREFIX",
        "PATH",
    }
    never_export = {"PWD", "LS_COLORS", "INVOCATION_ID", "SHLVL", "SHELL"}
    always_unset = {"DISPLAY", "WAYLAND_DISPLAY"}
    always_cleanup = {
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_SESSION_ID",
        "XDG_SESSION_TYPE",
        "XDG_VTNR",
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_DESKTOP",
        "XDG_MENU_PREFIX",
        "PATH",
        "XCURSOR_THEME",
        "XCURSOR_SIZE",
        "LANG",
    }
    never_cleanup = {"SSH_AGENT_LAUNCHER", "SSH_AUTH_SOCK", "SSH_AGENT_PID"}


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


def dedent(data: str) -> str:
    "Applies dedent, lstrips newlines, rstrips except single newline"
    data = textwrap.dedent(data).lstrip("\n")
    return data.rstrip() + "\n" if data.endswith("\n") else data.rstrip()


class HelpFormatterNewlines(argparse.HelpFormatter):
    "Treats double newlines as line breaks, preserves indents after them"

    def _fill_text(self, text, width, indent):
        "For parser descriptions and epilogs"
        lines = []
        for line in text.split("\n\n"):
            p_indent = line[0 : len(line) - len(line.lstrip())]
            lines.append(
                argparse.HelpFormatter._fill_text(self, line, width, indent + p_indent)
            )
        return "\n".join(lines)

    def _split_lines(self, text, width):
        "For argument descriptions"
        lines = []
        for line in text.split("\n\n"):
            p_indent = line[0 : len(line) - len(line.lstrip())]
            p_indent_width = len(p_indent)
            lines.extend(
                p_indent + l
                for l in argparse.HelpFormatter._split_lines(
                    self, line, width - p_indent_width
                )
            )
        return lines


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
    "Normal print with flush"
    print(*what, **how, flush=True)


def print_ok(*what, **how):
    "Prints in green (if interactive) to stdout"
    file = how.pop("file", sys.stdout)
    if file.isatty():
        print(Styles.green, end="", file=file, flush=True)
    print(*what, **how, file=file, flush=True)
    if file.isatty():
        print(Styles.reset, end="", file=file, flush=True)


def print_warning(*what, **how):
    "Prints in yellow (if interactive) to stdout"
    file = how.pop("file", sys.stdout)
    if file.isatty():
        print(Styles.yellow, end="", file=file, flush=True)
    print(*what, **how, file=file, flush=True)
    if file.isatty():
        print(Styles.reset, end="", file=file, flush=True)


def print_error(*what, **how):
    "Prints in red (if interactive) to stderr"
    file = how.pop("file", sys.stderr)
    if file.isatty():
        print(Styles.red, end="", file=file, flush=True)
    print(*what, **how, file=file, flush=True)
    if file.isatty():
        print(Styles.reset, end="", file=file, flush=True)


if int(os.getenv("DEBUG", "0")) > 0:
    from inspect import stack

    def print_debug(*what, **how):
        "Prints to stderr with DEBUG and END_DEBUG marks"
        dsep = "\n" if "sep" not in how or "\n" not in how["sep"] else ""
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


def print_error_or_traceback(exception, warning=False) -> None:
    "Depending on DEBUG, print nice error/warning or entire exception traceback"
    if int(os.getenv("DEBUG", "0")) > 0:
        file = sys.stdout if warning else sys.stderr
        if file.isatty():
            print(
                Styles.yellow if warning else Styles.red,
                end="",
                file=file,
                flush=True,
            )
        traceback.print_exception(exception, file=file)
        if file.isatty():
            print(Styles.reset, end="", file=file, flush=True)
    else:
        if warning:
            print_warning(exception)
        else:
            print_error(exception)


class MainArg:
    """
    Evaluates main argument string.
    Checks if it is entry_id or entry_id:action_id (and validate strings), or nothing like (executable), and if given as a path.
    Fills attributes: entry_id, entry_action, executable, path
    """

    def __init__(self, arg: str):
        "Takes argument string"

        self.entry_id, self.entry_action, self.path, self.executable = (
            None,
            None,
            None,
            None,
        )

        if arg is None:
            pass

        elif not isinstance(arg, str):
            raise ValueError(f"Expected str or None, got {type(arg)}: {arg}")

        # Desktop entry
        elif arg.endswith(".desktop") or ".desktop:" in arg:
            # separate action
            if ":" in arg:
                self.entry_id, entry_action = arg.split(":", maxsplit=1)
                if entry_action:
                    if not Val.action_id.search(entry_action):
                        raise ValueError(
                            f'Invalid desktop entry action "{entry_action}"'
                        )
                    self.entry_action = entry_action
            else:
                self.entry_id = arg

            # path to entry
            if "/" in self.entry_id:
                self.path = os.path.normpath(os.path.expanduser(self.entry_id))

                # start with an assumption that entry ID is basename
                self.entry_id = os.path.basename(self.entry_id)

                # only makes sense for applications
                # check if path is in 'applications' and extract proper entry ID
                for data_dir in BaseDirectory.load_data_paths("applications"):
                    relpath = os.path.relpath(self.path, data_dir)
                    if not relpath.startswith("../"):
                        self.entry_id = relpath.replace("/", "-")
                        break

            # validate id
            if not Val.entry_id.search(self.entry_id):
                raise ValueError(f'Invalid desktop entry ID "{self.entry_id}"')

        # Executable
        else:
            # executable is stored as is, with or without path
            self.executable = arg
            # mark path
            if "/" in arg:
                self.path = os.path.normpath(os.path.expanduser(arg))

    def __str__(self):
        "String representation for debug purposes"
        return (
            f"{self.__class__.__name__}("
            + ", ".join(
                f"{attr}={getattr(self, attr, None)}"
                for attr in ("entry_id", "entry_action", "executable", "path")
                if getattr(self, attr, None) is not None
            )
            + ")"
        )

    def check_path(self):
        "Checks if exists and has appropriate permissions. No path is OK."
        if self.path is None:
            return
        if not os.path.isfile(self.path):
            raise FileNotFoundError(f'Path "{self.path}" does not exist!')
        if not os.access(self.path, os.R_OK):
            raise PermissionError(f'Path "{self.path}" is not readable!')
        if self.executable and not os.access(self.path, os.X_OK):
            raise PermissionError(f'Path "{self.path}" is not executable!')
        print_debug(f"Path {self.path} OK")


def entry_action_keys(entry, entry_action=None):
    "Extracts Name, Exec, Icon from entry with or without entry_action"
    out = {"Name": entry.getName(), "Exec": entry.getExec(), "Icon": entry.getIcon()}
    if not entry_action:
        return out

    if entry_action not in entry.getActions():
        raise ValueError(
            f'entry "{entry.getFileName()}" does not have action "{entry_action}"'
        )

    # switch to action group
    entry.defaultGroup = f"Desktop Action {entry_action}"
    out.update(
        {
            "Name": entry.getName(),
            "Exec": entry.getExec(),
        }
    )
    if entry.getIcon():
        out.update({"Icon": entry.getIcon()})

    # restore default group
    entry.defaultGroup = "Desktop Entry"
    return out


def check_entry_basic(entry, entry_action=None):
    "Takes entry, performs basic checks, raises RuntimeError on failure"
    try:
        entry.validate()
    except ValidationError:
        pass
    errors = set()
    print_debug(
        f'entry "{entry.getFileName()}"',
        *(f"  err: {error}" for error in entry.errors),
        *(f"  wrn: {warning}" for warning in entry.warnings),
        *(["  all clear"] if not entry.errors and not entry.warnings else []),
        sep="\n",
    )
    # Be chill with some [stupid] errors
    for error in entry.errors:
        if " is not a registered " in error:
            continue
        if error in [
            # For proposed xdg-terminal-exec
            "Invalid key: ExecArg",
            # Accompanies unregistered categories
            "Missing main category",
            # Used in wayland-sessions
            "Invalid key: DesktopNames",
            # New keys in spec not yet known by pyxdg
            "Invalid key: DBusActivatable",
            "Invalid key: SingleMainWindow",
            "Invalid key: PrefersNonDefaultGPU",
            # Used in X-tended sections, but triggers errors anyway
            "Invalid key: TargetEnvironment",
        ]:
            continue
        errors.add(error)
    if errors:
        raise RuntimeError(
            "\n".join(
                [f"Entry {entry.getFileName()} failed validation:"]
                + [f"  err: {error}" for error in errors]
            )
        )
    if entry.getHidden():
        raise RuntimeError(f"Entry {entry.getFileName()} is hidden")
    if entry.hasKey("TryExec") and not entry.findTryExec():
        raise RuntimeError(f"Entry {entry.getFileName()} is discarded by TryExec")
    if entry_action:
        if entry_action not in entry.getActions():
            raise RuntimeError(
                f"Entry {entry.getFileName()} has no action {entry_action}"
            )
        entry_action_group = f"Desktop Action {entry_action}"
        if entry_action_group not in entry.groups():
            raise RuntimeError(
                f"Entry {entry.getFileName()} has no action group {entry_action_group}"
            )
        entry_dict = entry_action_keys(entry, entry_action)
        if "Name" not in entry_dict or not entry_dict["Name"]:
            raise RuntimeError(
                f"Entry {entry.getFileName()} action {entry_action} does not have Name"
            )
        if "Exec" not in entry_dict or not entry_dict["Exec"]:
            raise RuntimeError(
                f"Entry {entry.getFileName()} action {entry_action} does not have Exec"
            )
        entry_exec = entry_dict["Exec"]
    else:
        if not entry.hasKey("Exec") or not entry.getExec():
            raise RuntimeError(f"Entry {entry.getFileName()} does not have Exec")
        entry_exec = entry.getExec()
    if not which(shlex.split(entry_exec)[0]):
        raise RuntimeError(
            f"Entry {entry.getFileName()} points to missing executable {shlex.split(entry_exec)[0]}"
        )


def check_entry_showin(entry):
    "Takes entry, checks OnlyShowIn/NotShowIn against XDG_CURRENT_DESKTOP, raises RuntimeError on failure"
    xcd = set(sane_split(os.getenv("XDG_CURRENT_DESKTOP", ""), ":"))
    osi = set(entry.getOnlyShowIn())
    nsi = set(entry.getNotShowIn())
    if osi and osi.isdisjoint(xcd):
        raise RuntimeError(f"Entry {entry.getFileName()} discarded by OnlyShowIn")
    if nsi and not nsi.isdisjoint(xcd):
        raise RuntimeError(f"Entry {entry.getFileName()} discarded by NotShowIn")
    return True


def entry_parser_session(entry_id, entry_path):
    "parser for wayland-sessions entries, returns ('append', (entry_id, entry))"
    try:
        entry = DesktopEntry(entry_path)
    except:
        print_debug(f"failed parsing {entry_path}, skipping")
        return ("drop", None)
    try:
        check_entry_basic(entry)
        return ("append", (entry_id, entry))
    except RuntimeError:
        return ("drop", None)


def entry_parser_by_ids(entry_id, entry_path, match_entry_id, match_entry_action):
    """
    Takes entry_id, entry_path, match_entry_id, match_entry_action
    matches, performs basic checks if mached
    returns ('return', entry) on success, ('drop', None) if not matched,
    or raises RuntimeError on validation failure
    """
    # drop if not matched ID
    if entry_id != match_entry_id:
        print_debug("not an entry we are looking for")
        return ("drop", None)
    try:
        entry = DesktopEntry(entry_path)
    except:
        raise RuntimeError(
            f'Failed to parse entry "{match_entry_id}" from "{entry_path}"'
        )

    check_entry_basic(entry, match_entry_action)

    print_debug("matched and checked")
    return ("return", entry)


def entry_parser_terminal(
    entry_id: str, entry_path: str, explicit_terminals: List = None
):
    """
    Takes entry_id, entry_path,
    optionally takes entry_action and explicit_terminals list of tuples [(entry_id, entry_action)]
    checks if is applicable terminal.
    if explicit_terminals are given checks without DE checks, returns with action 'extend'
    (for multiple actions, further sorting)
      ('extend', [(entry, entry_id, entry_action)]) or ('drop', (None, None, None))
    if no explicit_terminals, returns with action 'return'
    (for first applicable entry)
      ('return', (entry, entry_id, None)) or ('drop', (None, None, None))
    """
    if explicit_terminals is None:
        explicit_terminals = []

    # drop if not among explicitly listed IDs
    if explicit_terminals and entry_id not in (i[0] for i in explicit_terminals):
        print_debug("not an entry we are looking for")
        return ("drop", (None, None, None))
    try:
        entry = DesktopEntry(entry_path)
    except:
        print_debug("failed to parse entry")
        Terminal.neg_cache.update({entry_path: os.path.getmtime(entry_path)})
        return ("drop", (None, None, None))

    # quick fail
    try:
        if "TerminalEmulator" not in entry.getCategories():
            print_debug("not a TerminalEmulator")
            Terminal.neg_cache.update({entry_path: os.path.getmtime(entry_path)})
            return ("drop", (None, None, None))
    except:
        print_debug("failed to get Categories")
        Terminal.neg_cache.update({entry_path: os.path.getmtime(entry_path)})
        return ("drop", (None, None, None))

    # get requested actions for this entry ID
    if explicit_terminals:
        results = []
        for entry_action in {i[1] for i in explicit_terminals if i[0] == entry_id}:
            try:
                check_entry_basic(entry, entry_action)
                # if this is the top choice, return right away
                if (entry_id, entry_action) == explicit_terminals[0]:
                    print_debug("bingo")
                    return ("return", (entry, entry_id, entry_action))
                # otherwise, add to cart
                results.append((entry, entry_id, entry_action))
            except RuntimeError:
                print_debug(f"action {entry_action} failed basic checks")
        if results:
            return ("extend", results)
        return ("drop", (None, None, None))

    # not explicit_terminals
    try:
        check_entry_basic(entry, None)
    except RuntimeError:
        print_debug("failed basic checks")
        Terminal.neg_cache.update({entry_path: os.path.getmtime(entry_path)})
        return ("drop", (None, None, None))
    try:
        check_entry_showin(entry)
    except RuntimeError:
        print_debug("failed ShowIn checks")
        # not adding to neg cache here
        return ("drop", (None, None, None))

    return ("return", (entry, entry_id, None))


def find_entries(
    subpath: str,
    parser: Callable = None,
    parser_args: dict = None,
    reject_pmt: dict = None,
):
    """
    Takes data hierarchy subpath and optional arg parser
    If parser is callable, it is called for each found entry with (entry_id, entry_path)
    Return is expected to be (action, data)
    action: what to do with the data: append|extend|return|drop(or anything else)
    By default returns list of tuples [(entry_id, entry_path)],
    otherwise returns whatever parser tells in a list
    """
    seen_ids = set()
    results = []
    if parser_args is None:
        parser_args = {}

    print_debug(f"searching entries in {subpath}")
    # iterate over data paths
    for data_dir in BaseDirectory.load_data_paths(subpath):
        # walk tree relative to data_dir
        for dirpath, _, filenames in os.walk(data_dir, followlinks=True):
            for filename in filenames:
                # fast crude rejection
                if not filename.endswith(".desktop"):
                    continue

                entry_path = os.path.join(dirpath, filename)

                # reject by path-mtime mapping
                if (
                    reject_pmt
                    and entry_path in reject_pmt
                    and os.path.getmtime(entry_path) == reject_pmt[entry_path]
                ):
                    print_debug(f"rejected {entry_path} by negative cache")
                    continue

                # get proper entry id relative to data_dir with path delimiters replaced by '-'
                entry_id = os.path.relpath(entry_path, data_dir).replace("/", "-")
                # get only valid IDs
                if not Val.entry_id.search(entry_id):
                    continue

                # id-based deduplication
                if entry_id in seen_ids:
                    print_debug(f"already seen {entry_id}")
                    continue
                seen_ids.add(entry_id)

                print_debug(f"considering {entry_id} {entry_path}")
                if callable(parser):
                    action, data = parser(entry_id, entry_path, **parser_args)
                else:
                    action, data = "append", (entry_id, entry_path)

                if action == "return":
                    return [data]
                if action == "append":
                    results.append(data)
                if action == "extend":
                    results.extend(data)

    print_debug(results)
    return results


def get_default_comp_entry():
    "Gets compositor desktop entry ID from {BIN_NAME}-default-id file in config hierarchy"
    for cmd_cache_file in BaseDirectory.load_config_paths(f"{BIN_NAME}-default-id"):
        if os.path.isfile(cmd_cache_file):
            try:
                with open(cmd_cache_file, "r", encoding="UTF-8") as cmd_cache_file:
                    for line in cmd_cache_file.readlines():
                        if line.strip():
                            wmid = line.strip()
                            return wmid
            except Exception as caught_exception:
                print_error_or_traceback(caught_exception)
                continue
    return ""


def save_default_comp_entry(default):
    "Gets saves compositor desktop entry ID from {BIN_NAME}-default-id file in config hierarchy"
    if "dry_run" not in Args.parsed or not Args.parsed.dry_run:
        if not os.path.isdir(BaseDirectory.xdg_config_home):
            os.mkdir(BaseDirectory.xdg_config_home)
        config = os.path.join(BaseDirectory.xdg_config_home, f"{BIN_NAME}-default-id")
        with open(config, "w", encoding="UTF-8") as config:
            config.write(default + "\n")
            print_ok(f"Saved default compositor ID: {default}.")
    else:
        print_ok(f"Would save default compositor ID: {default}.")


def select_comp_entry(default="", just_confirm=False):
    """
    Uses whiptail to select among "wayland-sessions" desktop entries.
    Takes a "default" to preselect, "just_confirm" flag to return a found default right away
    """

    if not which("whiptail"):
        raise FileNotFoundError(
            '"whiptail" is not in PATH, "select" and "default" are not supported'
        )

    choices_raw: List[tuple[str]] = []
    choices: List[str] = []

    # fill choces list with [entry_id, description, comment]
    for entry_id, entry in sorted(
        find_entries("wayland-sessions", parser=entry_parser_session)
    ):
        name: str = entry.getName()
        generic_name: str = entry.getGenericName()
        description: str = " ".join((n for n in (name, generic_name) if n))
        comment: str = entry.getComment()
        # add a choice
        choices_raw.append((entry_id, description, comment))

        # also enumerate actions
        for action in entry.getActions():
            print_debug("parsing aciton", action)
            action_group: str = f"Desktop Action {action}"
            if not entry.hasGroup(action_group):
                continue

            # switch to action group
            entry.defaultGroup = action_group
            if (
                not entry.getExec()
                or not which(shlex.split(str(entry.getExec()))[0])
                or not entry.getName()
            ):
                continue

            # action_description: str = f" ╰─▶ {entry.getName()}"
            # action_description: str = f" ╰▶ {entry.getName()}"
            action_description: str = f" └▶ {entry.getName()}"

            # add a choice
            choices_raw.append((f"{entry_id}:{action}", action_description, ""))

    # find longest description
    description_length: int = 0
    for choice in choices_raw:
        if len(choice[1]) > description_length:
            description_length = len(choice[1])

    # pretty format choices
    col_overhead = 10
    try:
        col = os.get_terminal_size().columns
    except OSError:
        print_warning("Could not get terminal width, assuming 128")
        col = 128
    except Exception as caught_exception:
        print_error_or_traceback(caught_exception)
        print_warning("Could not get terminal width, assuming 128")
        col = 128

    for choice in choices_raw:
        choices.append(choice[0])
        if choice[2]:
            choices.append(
                f"{choice[1].ljust(description_length)}  {textwrap.shorten(choice[2], col - description_length - col_overhead)}"
            )
        else:
            choices.append(choice[1])

    if not choices:
        raise RuntimeError("No choices found")

    if len(choices) % 2 != 0:
        raise ValueError(
            f"Choices for whiptail are not even ({len(choices)}): {choices}"
        )

    # drop default if not among choices
    if default and default not in choices[::2]:
        default = ""

    # just spit out default if requested and found
    if default and just_confirm:
        for choice in choices[::2]:
            if choice == default:
                return choice

    # no default default here, fail on noninteractive terminal
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        io = []
        if not sys.stdin.isatty():
            io.append("stdin")
        if not sys.stdout.isatty():
            io.append("stdout")
        raise IOError(
            f"{', '.join(io)} {'is' if len(io) == 1 else 'are'} not attached to interactive terminal! Can not launch menu!"
        )

    # generate arguments for whiptail exec
    argv = [
        "whiptail",
        "--clear",
        "--backtitle",
        "Universal Wayland Session Manager",
        "--title",
        "Choose compositor",
        # "--nocancel",
        *(("--default-item", default) if default else ""),
        "--menu",
        "",
        "0",
        "0",
        "0",
        "--notags",
        *(choices),
    ]

    # replace whiptail theme with simple default colors
    whiptail_env = dict(os.environ) | {"NEWT_MONO": "true"}
    # run whiptail, capture stderr
    sprc = subprocess.run(
        argv, env=whiptail_env, stderr=subprocess.PIPE, text=True, check=False
    )
    print_debug(sprc)

    return sprc.stderr.strip() if sprc.returncode == 0 and sprc.stderr else ""


def reload_systemd():
    "Reloads systemd user manager"

    if Args.parsed.dry_run:
        print_normal("Will reload systemd user manager.")
        UnitsState.changed = False
        return True

    # query systemd dbus for matching units
    print_normal("Reloading systemd user manager.")
    bus_session = DbusInteractions("session")
    print_debug("bus_session initial", bus_session)
    job = bus_session.reload_systemd()
    # wait for job to be done
    while True:
        jobs = bus_session.list_systemd_jobs()
        print_debug("current systemd jobs", jobs)
        if job not in [check_job[4] for check_job in jobs]:
            break
        time.sleep(0.1)

    print_debug(f"reload systemd job {job} finished")

    UnitsState.changed = False
    return True


def set_systemd_vars(vars_dict: dict):
    "Exports vars from given dict to systemd user manager"

    bus_session = DbusInteractions("session")
    print_debug("bus_session initial", bus_session)
    # check what dbus service is running
    # if it is not dbus-broker, also set dbus environmetn vars
    dbus_unit = bus_session.get_unit_property("dbus.service", "Id")
    print_debug("dbus.service Id", dbus_unit)
    if dbus_unit != "dbus-broker.service":
        print_debug(
            "dbus unit", dbus_unit, "managing separate dbus activation environment"
        )
        print_debug("sending to .UpdateActivationEnvironment", vars_dict)
        bus_session.set_dbus_vars(vars_dict)

    bus_session.set_systemd_vars(vars_dict)


def unset_systemd_vars(vars_list):
    "Unsets vars from given list from systemd user manager"
    bus_session = DbusInteractions("session")
    print_debug("bus_session initial", bus_session)

    dbus_unit = bus_session.get_unit_property("dbus.service", "Id")
    print_debug("dbus.service Id", dbus_unit)
    if dbus_unit != "dbus-broker.service":
        print_debug(
            "dbus unit", dbus_unit, "managing separate dbus activation environment"
        )

        vars_dict = {}
        for var in vars_list:
            vars_dict.update({var: ""})

        print_debug("sending to .UpdateActivationEnvironment", vars_dict)
        bus_session.set_dbus_vars(vars_dict)

    bus_session.unset_systemd_vars(vars_list)


def char2cesc(string: str) -> str:
    "Takes a string, returns c-style '\\xXX' sequence"
    return "".join("\\x%02x" % b for b in bytes(string, "utf-8"))


def simple_systemd_escape(string: str, start: bool = True) -> str:
    """
    Escapes simple strings by systemd rules.
    Set 'start=False' if string is not intended for start of resutling string
    """
    out = []
    # escape '.' if starts with it
    if start and string.startswith("."):
        out.append(char2cesc("."))
        string = string[1:]
    for ch, ucp in ((c, ord(c)) for c in string):
        # replace '/' with '-'
        if ch == "/":
            out.append("-")
        # append as is if ._0-9:A-Z
        elif ucp in [46, 95] or 48 <= ucp <= 58 or 65 <= ucp <= 90 or 97 <= ucp <= 122:
            out.append(ch)
        # append c-style escape
        else:
            out.append(char2cesc(ch))
    return "".join(out)


def get_unit_path(unit: str, category: str = "runtime", level: str = "user"):
    "Returns tuple: 0) path in category, level dir, 1) unit subpath"
    if os.path.isabs(unit):
        raise RuntimeError("Passed absolute path to get_unit_path")

    unit = os.path.normpath(unit)

    unit_path: str = ""
    if category == "runtime":
        try:
            unit_path = BaseDirectory.get_runtime_dir(strict=True)
        except:
            pass
        if not unit_path:
            print_error("Fatal: empty or undefined XDG_RUNTIME_DIR!")
            sys.exit(0)
    else:
        raise RuntimeError(f"category {category} is not supported")

    if level not in ["user"]:
        raise RuntimeError(f"level {level} is not supported")

    unit_dir = os.path.normpath(os.path.join(unit_path, "systemd", level))
    return (unit_dir, unit)


def get_active_wm_unit(active=True, activating=True):
    """
    Finds activating or active wayland-wm@*.service, returns unit ID.
    Bool strict_active to match only active state.
    """
    bus_session = DbusInteractions("session")
    print_debug("bus_session initial", bus_session)

    # query systemd dbus for matching units
    units = bus_session.list_units_by_patterns(
        (["active"] if active else []) + (["activating"] if activating else []),
        ["wayland-wm@*.service"],
    )

    if len(units) == 0:
        return ""
    if len(units) == 1:
        return str(units[0][0])
    if len(units) > 1:
        print_warning(
            f"Got more than 1 active wayland-wm@*.service: {', '.join(units)}"
        )
        return str(units[0][0])


def get_active_wm_id(active=True, activating=True):
    "Finds running wayland-wm@*.service, returns specifier"

    active_id = get_active_wm_unit(active, activating)

    if active_id:
        # extract and unescape specifier
        active_id = active_id.split("@")[1].removesuffix(".service")
        active_id = bytes(active_id, "UTF-8").decode("unicode_escape")
        return active_id

    return ""


def is_active(check_wm_id="", verbose=False, verbose_active=False):
    """
    Checks if units are active or activating, returns bool.
    If check_wm_id is empty, checks graphical*.target and wayland-wm@*.service
    If check_wm_id is "compositor-only", checks wayland-wm@*.service
    If check_wm_id has other value, checks unit of specific wm_id
    verbose=True prints matched or relevant units
    verbose_active=True prints matched active units
    """
    bus_session = DbusInteractions("session")
    print_debug("bus_session initial", bus_session)

    check_units_generic = [
        "graphical-session-pre.target",
        "wayland-session-pre@*.target",
        "graphical-session.target",
        "wayland-session@*.target",
        "wayland-wm@*.service",
    ]
    if check_wm_id and check_wm_id == "compositor-only":
        check_units = ["wayland-wm@*.service"]
    elif check_wm_id:
        # escape wm_id for systemd
        check_wm_id_unit_string = simple_systemd_escape(check_wm_id, start=False)
        check_units = [f"wayland-wm@{check_wm_id_unit_string}.service"]
    else:
        check_units = check_units_generic

    # query systemd dbus for matching units
    units = bus_session.list_units_by_patterns([], check_units)
    # extract strings
    active_units = []
    inactive_units = []
    for unit in units:
        if str(unit[3]) in ["active", "activating"]:
            active_units.append(
                (str(unit[0]), str(unit[1]), str(unit[3]), str(unit[4]))
            )
        else:
            inactive_units.append(
                (str(unit[0]), str(unit[1]), str(unit[3]), str(unit[4]))
            )

    if active_units:
        if verbose or verbose_active:
            print_normal(f"Matched {len(units)} units, {len(active_units)} active:")
            for name, descr, state, substate in active_units:
                print_normal(f"  {name} ({descr})")
            if verbose:
                if inactive_units:
                    print_normal(f"{len(inactive_units)} inactive:")
                for name, descr, state, substate in inactive_units:
                    print_normal(f"  {name} ({descr})")
        return True

    if not verbose:
        return False

    # just show generic check if above came empty and in verbose mode
    # query systemd dbus for matching units
    units = bus_session.list_units_by_patterns([], check_units_generic)
    # extract strings
    active_units = []
    inactive_units = []
    for unit in units:
        if str(unit[3]) in ["active", "activating"]:
            active_units.append(
                (str(unit[0]), str(unit[1]), str(unit[3]), str(unit[4]))
            )
        else:
            inactive_units.append(
                (str(unit[0]), str(unit[1]), str(unit[3]), str(unit[4]))
            )
    print_normal("No units matched. Listing other relevant units:")
    if active_units:
        print_normal(f"  {len(active_units)} active:")
        for name, descr, state, substate in active_units:
            print_normal(f"    {name} ({descr})")
    if inactive_units:
        print_normal(f"  {len(inactive_units)} inactive:")
        for name, descr, state, substate in inactive_units:
            print_normal(f"    {name} ({descr})")
    return False


def update_unit(unit, data):
    """
    Updates unit with data if differs
    Returns change in boolean
    """

    if not Val.unit_ext.search(unit):
        raise ValueError(
            f"Trying to update unit with unsupported extension {unit.split('.')[-1]}: {unit}"
        )

    if os.path.isabs(unit):
        unit_dir, unit = ("/", os.path.normpath(unit))
    else:
        if unit.count("/") > 1:
            raise ValueError(
                f"Only single subdir supported for relative unit, got {unit.count('/')} ({unit})"
            )
        unit_dir, unit = get_unit_path(unit)
    unit_path = os.path.join(unit_dir, unit)

    # create subdirs if missing
    check_dir = unit_dir
    if not os.path.isdir(check_dir):
        if not Args.parsed.dry_run:
            os.mkdir(check_dir)
            print_ok(f'Created dir "{check_dir}/"')
        elif not UnitsState.changed:
            print_ok(f'Will create dir "{check_dir}/"')
    for path_element in [d for d in os.path.dirname(unit).split(os.path.sep) if d]:
        check_dir = os.path.join(check_dir, path_element)
        if not os.path.isdir(check_dir):
            if not Args.parsed.dry_run:
                os.mkdir(check_dir)
                print_ok(f'Created unit subdir "{path_element}/"')
            else:
                print_ok(f'Will create unit subdir "{path_element}/"')

    old_data = ""
    if os.path.isfile(unit_path):
        with open(unit_path, "r", encoding="UTF-8") as unit_file:
            old_data = unit_file.read()

    if data == old_data:
        return False

    if not Args.parsed.dry_run:
        with open(unit_path, "w", encoding="UTF-8") as unit_file:
            unit_file.write(data)
        print_ok(f'Updated "{unit}".')
    else:
        print_ok(f'Will update "{unit}".')

    print_debug(data)

    UnitsState.changed = True
    return True


def remove_unit(unit):
    "Removes unit and subdir if empty"

    if not Val.unit_ext.search(unit):
        raise ValueError(
            f"Trying to remove unit with unsupported extension {unit.split('.')[-1]}"
        )

    if os.path.isabs(unit):
        unit_dir, unit = ("/", os.path.normpath(unit))
    else:
        if unit.count("/") > 1:
            raise ValueError(
                f"Only single subdir supported for relative unit, got {unit.count('/')} ({unit})"
            )
        unit_dir, unit = get_unit_path(unit)
    unit_path = os.path.join(unit_dir, unit)

    change = False
    # remove unit file
    if os.path.isfile(unit_path):
        if not Args.parsed.dry_run:
            os.remove(unit_path)
            print_ok(f"Removed unit {unit}.")
        else:
            print_ok(f"Will remove unit {unit}.")
        UnitsState.changed = True
        change = True

    # deal with subdir
    if not os.path.isabs(unit) and "/" in unit:
        unit_subdir_path = os.path.dirname(unit_path)
        unit_subdir = os.path.dirname(unit)
        unit_filename = os.path.basename(unit_path)
        if os.path.isdir(unit_subdir_path):
            if set(os.listdir(unit_subdir_path)) - {unit_filename}:
                print_warning(f"Unit subdir {unit_subdir} is not empty.")
            else:
                if not Args.parsed.dry_run:
                    os.rmdir(unit_subdir_path)
                    print_ok(f"Removed unit subdir {unit_subdir}.")
                else:
                    print_ok(f"Will remove unit subdir {unit_subdir}.")

    return change


def generate_units():
    # sourcery skip: assign-if-exp, extract-duplicate-method, remove-redundant-if, split-or-ifs
    "Generates basic unit structure"

    if Args.parsed.use_session_slice:
        wayland_wm_slice = "session.slice"
    else:
        wayland_wm_slice = "app.slice"

    UnitsState.changed = False

    # targets
    update_unit(
        "wayland-session-pre@.target",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=Preparation for session of %I Wayland compositor
            Documentation=man:systemd.special(7)
            Requires=basic.target
            StopWhenUnneeded=yes
            BindsTo=graphical-session-pre.target
            Before=graphical-session-pre.target
            PropagatesStopTo=graphical-session-pre.target
            """
        ),
    )
    update_unit(
        "wayland-session@.target",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=Session of %I Wayland compositor
            Documentation=man:systemd.special(7)
            Requires=wayland-session-pre@%i.target graphical-session-pre.target
            After=wayland-session-pre@%i.target graphical-session-pre.target
            StopWhenUnneeded=yes
            BindsTo=graphical-session.target
            Before=graphical-session.target
            PropagatesStopTo=graphical-session.target
            """
        ),
    )
    update_unit(
        "wayland-session-xdg-autostart@.target",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=XDG Autostart for session of %I Wayland compositor
            Documentation=man:systemd.special(7)
            Requires=wayland-session@%i.target graphical-session.target
            After=wayland-session@%i.target graphical-session.target
            StopWhenUnneeded=yes
            BindsTo=xdg-desktop-autostart.target
            Before=xdg-desktop-autostart.target
            PropagatesStopTo=xdg-desktop-autostart.target
            """
        ),
    )
    update_unit(
        "wayland-session-shutdown.target",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=Shutdown graphical session units
            Documentation=man:systemd.special(7)
            DefaultDependencies=no
            Conflicts=app-graphical.slice
            After=app-graphical.slice
            Conflicts=background-graphical.slice
            After=background-graphical.slice
            Conflicts=session-graphical.slice
            After=session-graphical.slice
            Conflicts=xdg-desktop-autostart.target
            After=xdg-desktop-autostart.target
            # dirty fix of xdg-desktop-portal-gtk.service shudown
            Conflicts=xdg-desktop-portal-gtk.service
            After=xdg-desktop-portal-gtk.service
            Conflicts=graphical-session.target
            After=graphical-session.target
            Conflicts=graphical-session-pre.target
            After=graphical-session-pre.target
            StopWhenUnneeded=yes
            """
        ),
    )

    # services
    update_unit(
        "wayland-wm-env@.service",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=Environment preloader for %I
            Documentation=man:systemd.service(7)
            BindsTo=wayland-session-pre@%i.target
            Before=wayland-session-pre@%i.target
            StopWhenUnneeded=yes
            CollectMode=inactive-or-failed
            OnFailure=wayland-session-shutdown.target
            OnSuccess=wayland-session-shutdown.target
            [Service]
            Type=oneshot
            RemainAfterExit=yes
            ExecStart={BIN_PATH} aux prepare-env "%I"
            ExecStop={BIN_PATH} aux cleanup-env
            Restart=no
            SyslogIdentifier={BIN_NAME}_env-preloader
            Slice={wayland_wm_slice}
            """
        ),
    )
    update_unit(
        "wayland-wm@.service",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=Main service for %I
            Documentation=man:systemd.service(7)
            BindsTo=wayland-session@%i.target
            Before=wayland-session@%i.target
            Requires=wayland-wm-env@%i.service graphical-session-pre.target
            After=wayland-wm-env@%i.service graphical-session-pre.target
            Wants=wayland-session-xdg-autostart@%i.target xdg-desktop-autostart.target
            Before=wayland-session-xdg-autostart@%i.target xdg-desktop-autostart.target app-graphical.slice background-graphical.slice session-graphical.slice
            PropagatesStopTo=app-graphical.slice background-graphical.slice session-graphical.slice
            # dirty fix of xdg-desktop-portal-gtk.service shudown
            PropagatesStopTo=xdg-desktop-portal-gtk.service
            CollectMode=inactive-or-failed
            OnFailure=wayland-session-shutdown.target
            OnSuccess=wayland-session-shutdown.target
            [Service]
            # awaits for 'systemd-notify --ready' from compositor child
            # should be issued by '{BIN_NAME} finalize'
            Type=notify
            NotifyAccess=all
            ExecStart={BIN_PATH} aux exec %I
            Restart=no
            TimeoutStartSec=10
            TimeoutStopSec=10
            SyslogIdentifier={BIN_NAME}_%I
            Slice={wayland_wm_slice}
            """
        ),
    )
    update_unit(
        "wayland-wm-app-daemon.service",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=Fast application argument generator
            Documentation=man:systemd.service(7)
            BindsTo=graphical-session.target
            CollectMode=inactive-or-failed
            [Service]
            Type=exec
            ExecStart={BIN_PATH} aux app-daemon
            Restart=on-failure
            RestartMode=direct
            SyslogIdentifier={BIN_NAME}_app-daemon
            Slice={wayland_wm_slice}
            """
        ),
    )

    # slices
    update_unit(
        "app-graphical.slice",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=User Graphical Application Slice
            Documentation=man:systemd.special(7)
            PartOf=graphical-session.target
            After=graphical-session.target
            """
        ),
    )
    update_unit(
        "background-graphical.slice",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=User Graphical Background Application Slice
            Documentation=man:systemd.special(7)
            PartOf=graphical-session.target
            After=graphical-session.target
            """
        ),
    )
    update_unit(
        "session-graphical.slice",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=User Graphical Session Application Slice
            Documentation=man:systemd.special(7)
            PartOf=graphical-session.target
            After=graphical-session.target
            """
        ),
    )

    # compositor-specific additions from cli or desktop entry via drop-ins
    wm_specific_preloader = (
        f"wayland-wm-env@{CompGlobals.id_unit_string}.service.d/50_custom.conf"
    )
    wm_specific_service = (
        f"wayland-wm@{CompGlobals.id_unit_string}.service.d/50_custom.conf"
    )
    wm_specific_preloader_data = [
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID={CompGlobals.id}
            """
        )
    ]
    wm_specific_service_data = [
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID={CompGlobals.id}
            """
        )
    ]

    # name is given
    if CompGlobals.name:
        wm_specific_preloader_data.append(
            dedent(
                f"""
                Description=Environment preloader for {CompGlobals.name}
                """
            )
        )

    # name or description is given
    if CompGlobals.name or CompGlobals.description:
        wm_specific_service_data.append(
            dedent(
                f"""
                Description=Main service for {', '.join((s for s in (CompGlobals.name or CompGlobals.cmdline[0], CompGlobals.description) if s))}
                """
            )
        )

    # exclusive desktop names were given on command line
    if CompGlobals.cli_desktop_names_exclusive:
        prepend: str = f" -eD \"{':'.join(CompGlobals.cli_desktop_names)}\""
    # desktop names differ from just executable name
    elif CompGlobals.desktop_names != [CompGlobals.cmdline[0]]:
        prepend: str = f" -D \"{':'.join(CompGlobals.desktop_names)}\""
    else:
        prepend: str = ""

    # additional args were given on cli
    append: str = f" {shlex.join(CompGlobals.cli_args)}" if CompGlobals.cli_args else ""

    if prepend or append:
        wm_specific_preloader_data.append(
            dedent(
                f"""
                [Service]
                ExecStart=
                ExecStart={BIN_PATH} aux prepare-env{prepend} "%I"{append}
                """
            )
        )

    if append:
        wm_specific_service_data.append(
            dedent(
                f"""
                [Service]
                ExecStart=
                ExecStart={BIN_PATH} aux exec "%I"{append}
                """
            )
        )

    if len(wm_specific_preloader_data) > 1:
        # add preloader customization tweak
        update_unit(
            wm_specific_preloader,
            # those strings already have newlines
            "".join(wm_specific_preloader_data),
        )
    else:
        # remove customization tweak
        remove_unit(wm_specific_preloader)

    if len(wm_specific_service_data) > 1:
        # add main service customization tweak
        update_unit(
            wm_specific_service,
            # those strings already have newlines
            "".join(wm_specific_service_data),
        )
    else:
        # remove customization tweak
        remove_unit(wm_specific_service)

    # tweaks
    update_unit(
        "app-@autostart.service.d/slice-tweak.conf",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            # make autostart apps stoppable by target
            #StopPropagatedFrom=xdg-desktop-autostart.target
            PartOf=xdg-desktop-autostart.target
            X-UWSM-ID=GENERIC
            [Service]
            # also put them in special graphical app slice
            Slice=app-graphical.slice
            """
        ),
    )
    # this does not work
    # update_unit(
    #     "xdg-desktop-portal-gtk.service.d/part-tweak.conf",
    #     dedent(
    #        f"""
    #        # injected by {BIN_NAME}, do not edit
    #        [Unit]
    #        # make the same thing as -wlr portal to stop correctly
    #        PartOf=graphical-session.target
    #        After=graphical-session.target
    #        ConditionEnvironment=WAYLAND_DISPLAY
    #        X-UWSM-ID=GENERIC
    #        """
    #     )
    # )
    # this breaks xdg-desktop-portal-rewrite-launchers.service
    # update_unit(
    #     "xdg-desktop-portal-.service.d/slice-tweak.conf",
    #     dedent(
    #        f"""
    #        # injected by {BIN_NAME}, do not edit
    #        [Service]
    #        # make xdg-desktop-portal-*.service implementations part of graphical scope
    #        Slice=app-graphical.slice
    #        X-UWSM-ID=GENERIC
    #        """
    #     )
    # )


def remove_units(only=None) -> None:
    """
    Removes units by X-UWSM-ID= attribute.
    if wm_id is given as argument, only remove X-UWSM-ID={wm_id}, else remove all.
    """
    if not only:
        only = ""
    mark_attr = f"X-UWSM-ID={only}"
    check_dir, _ = get_unit_path("")
    unit_files = []

    for directory, _, files in sorted(os.walk(check_dir)):
        for file_name in sorted(files):
            file_path = os.path.join(directory, file_name)
            if not os.path.isfile(file_path):
                print_debug("skipping, not a file:", file_path)
                continue
            print_debug("checking for removal:", file_path)
            try:
                with open(file_path, "r", encoding="UTF=8") as unit_file:
                    for line in unit_file.readlines():
                        if (only and line.strip() == mark_attr) or (
                            not only and line.strip().startswith(mark_attr)
                        ):
                            unit_files.append(
                                file_path.removeprefix(check_dir.rstrip("/") + "/")
                            )
                            print_debug(f"found {mark_attr}")
                            break
            except:
                pass

    for file_path in unit_files:
        remove_unit(file_path)


class Args:
    """
    Parses args. Stores attributes 'parsers' and 'parsed'. Globally for main args, instanced for custom args.
    """

    parsers = argparse.Namespace()
    parsed = argparse.Namespace()

    def __init__(self, custom_args=None, exit_on_error=True, store_parsers=False):
        "Parses sys.argv[1:] or custom_args"

        print_debug(
            f"parsing {'argv' if custom_args is None else 'custom args'}",
            sys.argv[1:] if custom_args is None else custom_args,
            f"exit_on_error: {exit_on_error}",
        )

        # keep parsers in a dict
        parsers = {}

        # main parser with subcommands
        parsers["main"] = argparse.ArgumentParser(
            formatter_class=HelpFormatterNewlines,
            description=dedent(
                """
                Universal Wayland Session Manager.\n
                \n
                Launches arbitrary wayland compositor via a set of systemd user units
                to provide graphical user session with environment management,
                XDG autostart support, scoped application launch helpers,
                clean shutdown.
                """
            ),
            # usage='%(prog)s [-h] action ...',
            epilog=dedent(
                f"""
                Also see "{BIN_NAME} {{subcommand}} -h" for further info.\n
                \n
                Compositor should finalize its startup by running this:\n
                \n
                  {BIN_NAME} finalize [[VAR] ANOTHER_VAR]\n
                \n
                (See "{BIN_NAME} finalize --help")\n
                \n
                Startup can be integrated conditionally into shell profile
                (See "{BIN_NAME} check --help").\n
                \n
                During startup at stage of "graphical-session-pre.target" environment is
                sourced from shell profile and from files "{BIN_NAME}-env" and
                "{BIN_NAME}-env-${{compositor}}" in XDG config hierarchy
                (in order of increasing importance). Delta will be exported to systemd and
                dbus activation environments, and cleaned up when services are stopped.\n
                \n
                It is highly recommended to configure your compositor to launch apps explicitly scoped
                in special user session slices (app.slice, background.slice, session.slice).
                {BIN_NAME} provides custom nested slices for apps to live in and be
                terminated on session end:\n
                \n
                  app-graphical.slice\n
                  background-graphical.slice\n
                  session-graphical.slice\n
                \n
                And a helper command to handle all the systemd-run invocations for you:
                (See "{BIN_NAME} app --help", "man systemd.special", "man systemd-run"\n
                \n
                If app launching is configured as recommended, you can put compositor itself in
                session.slice (as recommended by man systemd.special) by adding "-S" to "start"
                subcommand, or setting:\n
                \n
                  UWSM_USE_SESSION_SLICE=true\n
                \n
                This var affects unit generation phase during start, Slice= parameter
                of compositor services (best to export it in shell profile before "{BIN_NAME}",
                or add to environment.d (see "man environment.d")).
                """
            ),
            exit_on_error=exit_on_error,
        )
        parsers["main_subparsers"] = parsers["main"].add_subparsers(
            title="Action subcommands",
            description=None,
            dest="mode",
            metavar="{subcommand}",
            required=True,
        )

        # compositor arguments for potential reuse via parents
        parsers["wm_args"] = argparse.ArgumentParser(
            add_help=False,
            formatter_class=HelpFormatterNewlines,
            exit_on_error=exit_on_error,
        )
        parsers["wm_args"].add_argument(
            "wm_cmdline",
            metavar="args",
            nargs="+",
            help=dedent(
                """
                Compositor command line. The first argument acts as an ID and should be either one of:\n
                  - executable name\n
                  - desktop entry ID (optionally with ":"-delimited action ID)\n
                  - special value "select" or "default"\n
                """
            ),
        )
        parsers["wm_args"].add_argument(
            "-D",
            metavar="name[:name...]",
            dest="desktop_names",
            default="",
            help="Names to fill XDG_CURRENT_DESKTOP with (:-separated).\n\nExisting var content is a starting point if no active session is running.",
        )
        parsers["wm_args_dn_exclusive"] = parsers[
            "wm_args"
        ].add_mutually_exclusive_group()
        parsers["wm_args_dn_exclusive"].add_argument(
            "-a",
            dest="desktop_names_exclusive",
            action="store_false",
            default=False,
            help="Append desktop names set by -D to other sources (default).",
        )
        parsers["wm_args_dn_exclusive"].add_argument(
            "-e",
            dest="desktop_names_exclusive",
            action="store_true",
            default=False,
            help="Use desktop names set by -D exclusively, discard other sources.",
        )
        parsers["wm_args"].add_argument(
            "-N",
            metavar="Name",
            dest="wm_name",
            default="",
            help="Fancy name for compositor (filled from desktop entry by default).",
        )
        parsers["wm_args"].add_argument(
            "-C",
            metavar="Comment",
            dest="wm_comment",
            default="",
            help="Fancy description for compositor (filled from desktop entry by default).",
        )

        # select subcommand
        parsers["select"] = parsers["main_subparsers"].add_parser(
            "select",
            formatter_class=HelpFormatterNewlines,
            help="Select default compositor entry",
            description="Invokes whiptail menu for selecting wayland-sessions desktop entries.",
            epilog=dedent(
                f"""
                Invokes a whiptail menu to select a default session among desktop entries in
                wayland-sessions XDG data hierarchy. Writes to ${{XDG_CONFIG_HOME}}/{BIN_NAME}-default-id
                Nothing else is done.
                """
            ),
        )

        # start subcommand
        parsers["start"] = parsers["main_subparsers"].add_parser(
            "start",
            formatter_class=HelpFormatterNewlines,
            help="Start compositor",
            description="Generates units for given compositor command line or desktop entry and starts compositor.",
            parents=[parsers["wm_args"]],
            epilog=dedent(
                f"""
                During "graphical-session-pre.target" activation the environment is
                sourced from:\n
                  - shell profile\n
                  - "{BIN_NAME}-env", "{BIN_NAME}-env-${{compositor}}" files
                  in XDG config hierarchy (in order of increasing importance).\n
                Delta is exported to systemd and dbus activation environments,
                and cleaned up when "graphical-session-pre.target" is deactivated.
                """
            ),
        )
        use_session_slice = os.getenv("UWSM_USE_SESSION_SLICE", "false")
        if use_session_slice not in ("true", "false"):
            print_warning(
                f'invalid UWSM_USE_SESSION_SLICE value "{use_session_slice}" ignored, set to "false".'
            )
            use_session_slice = "false"
        parsers["start_slice"] = parsers["start"].add_mutually_exclusive_group()
        parsers["start_slice"].add_argument(
            "-S",
            action="store_true",
            dest="use_session_slice",
            default=use_session_slice == "true",
            help=f"Launch compositor in session.slice{' (already preset by UWSM_USE_SESSION_SLICE env var)' if use_session_slice == 'true' else ''}.",
        )
        parsers["start_slice"].add_argument(
            "-A",
            action="store_false",
            dest="use_session_slice",
            default=use_session_slice == "true",
            help=f"Launch compositor in app.slice{' (already preset by UWSM_USE_SESSION_SLICE env var)' if use_session_slice == 'false' else ''}.",
        )
        parsers["start"].add_argument(
            "-o",
            action="store_true",
            dest="only_generate",
            help="Only generate units, but do not start.",
        )
        parsers["start"].add_argument(
            "-n",
            action="store_true",
            dest="dry_run",
            help="Do not write or start anything.",
        )

        # stop subcommand
        parsers["stop"] = parsers["main_subparsers"].add_parser(
            "stop",
            formatter_class=HelpFormatterNewlines,
            help="Stop compositor",
            description="Stops compositor and optionally removes generated units.",
            epilog=dedent(
                """
                During "graphical-session-pre.target" deactivation
                environment is cleaned up from systemd activation environment according
                to a list saved in ${XDG_RUNTIME_DIR}/env_names_for_cleanup_* files.
                """
            ),
        )
        parsers["stop"].add_argument(
            "-r",
            nargs="?",
            metavar="wm,wm.desktop[:action]",
            default=False,
            dest="remove_units",
            help="Also remove units (all or only compositor-specific).",
        )
        parsers["stop"].add_argument(
            "-n",
            action="store_true",
            dest="dry_run",
            help="Do not write or start anything.",
        )

        # finalize subcommand
        parsers["finalize"] = parsers["main_subparsers"].add_parser(
            "finalize",
            formatter_class=HelpFormatterNewlines,
            help="Signal successful compositor startup, export essential and optional variables",
            description="For use inside compositor to export variables and signal successful startup.",
            epilog=dedent(
                """
                Exports WAYLAND_DISPLAY, DISPLAY, and any optional variables
                (mentioned by name as arguments) to systemd user manager.\n
                \n
                Variables are also added to cleanup list for stop phase.\n
                \n
                If all is well, sends startup notification to systemd user manager,
                so compositor unit is considered started and graphical-session.target can be declared reached.
                """
            ),
        )
        parsers["finalize"].add_argument(
            "env_names",
            metavar="[ENV_NAME [ENV2_NAME ...]]",
            nargs="*",
            help="Additional vars to export.",
        )

        # app subcommand
        parsers["app"] = parsers["main_subparsers"].add_parser(
            "app",
            formatter_class=HelpFormatterNewlines,
            help="Scoped app launcher",
            description="Launches application as a scope or service in specific slice.",
        )
        parsers["app"].add_argument(
            "cmdline",
            metavar="args",
            # allow empty cmdline if '-T' is given and comes before '--'
            nargs=(
                "*"
                if [
                    arg
                    for arg in (sys.argv[1:] if custom_args is None else custom_args)
                    if arg in ("-T", "--")
                ][0:1]
                == ["-T"]
                else "+"
            ),
            help=dedent(
                """
                Applicatoin command line. The first argument can be either one of:\n
                  - executable name or path\n
                  - desktop entry ID (with optional ":"-delimited action ID)\n
                  - path to desktop entry file (with optional ":"-delimited action ID)\n
                """
            ),
        )
        parsers["app"].add_argument(
            "-s",
            dest="slice_name",
            metavar="{a,b,s,custom.slice}",
            help=dedent(
                f"""
                Slice selector:\n
                  - {Styles.under}a{Styles.reset}pp-graphical.slice\n
                  - {Styles.under}b{Styles.reset}ackground-graphical.slice\n
                  - {Styles.under}s{Styles.reset}ession-graphical.slice\n
                  - custom by full name\n
                (default: %(default)s)
                """
            ),
            default="a",
        )
        app_unit_type_preset = False
        app_unit_type_default = os.getenv("UWSM_APP_UNIT_TYPE", None)
        if app_unit_type_default in ("scope", "service"):
            app_unit_type_preset = True
        elif app_unit_type_default is not None:
            print_warning(
                f'invalid UWSM_APP_UNIT_TYPE value "{app_unit_type_default}" ignored, set to "scope".'
            )
            app_unit_type_default = "scope"
        else:
            app_unit_type_default = "scope"
        parsers["app"].add_argument(
            "-t",
            dest="app_unit_type",
            choices=("scope", "service"),
            default=app_unit_type_default,
            help=f"Type of unit to launch (default: %(default)s, {'was' if app_unit_type_preset else 'can be'} preset by UWSM_APP_UNIT_TYPE env var).",
        )
        parsers["app"].add_argument(
            "-a",
            dest="app_name",
            metavar="app_name",
            help="Override app name (a substring in unit name).",
            default="",
        )
        parsers["app"].add_argument(
            "-u",
            dest="unit_name",
            metavar="unit_name",
            help="Override the whole autogenerated unit name.",
            default="",
        )
        parsers["app"].add_argument(
            "-d",
            dest="unit_description",
            metavar="unit_description",
            help="Unit Description.",
            default="",
        )
        parsers["app"].add_argument(
            "-T",
            dest="terminal",
            action="store_true",
            help="Launch app in a terminal, or just a terminal if command is empty.",
        )

        # check subcommand
        parsers["check"] = parsers["main_subparsers"].add_parser(
            "check",
            formatter_class=HelpFormatterNewlines,
            help="Checkers of states",
            description="Performs a check, returns 0 if true, 1 if false.",
            epilog=dedent(
                f"""
                Use may-start checker to integrate startup into shell profile
                See "{BIN_NAME} check may-start -h"
                """
            ),
        )
        parsers["check_subparsers"] = parsers["check"].add_subparsers(
            title="Subcommands",
            description=None,
            dest="checker",
            metavar="{checker}",
            required=True,
        )
        parsers["is_active"] = parsers["check_subparsers"].add_parser(
            "is-active",
            formatter_class=HelpFormatterNewlines,
            help="checks for active compositor",
            description="Checks for specific compositor or graphical-session*.target in general in active or activating state",
        )
        parsers["is_active"].add_argument(
            "wm",
            nargs="?",
            help="Specify compositor by executable or desktop entry (without arguments).",
        )
        parsers["is_active"].add_argument(
            "-v", action="store_true", dest="verbose", help="Show additional info."
        )

        parsers["may_start"] = parsers["check_subparsers"].add_parser(
            "may-start",
            formatter_class=HelpFormatterNewlines,
            help="Checks for start conditions",
            description="Checks whether it is OK to launch a wayland session.",
            epilog=dedent(
                f"""
                Conditions:\n
                  - Running from login shell\n
                  - System is at graphical.target\n
                  - User graphical-session*.target are not yet active\n
                  - Foreground VT is among allowed (default: 1)\n
                \n
                To integrate startup into shell profile, add:\n
                \n
                  if {BIN_NAME} check may-start && {BIN_NAME} select\n
                  then\n
                  	exec {BIN_NAME} start select\n
                  fi\n
                \n
                Condition is essential, since {BIN_NAME}'s environment preloader sources
                profile and can cause loops without protection.\n
                \n
                Separate select action allows droping to normal shell.\n
                \n
                If the only failed condition is already active user graphical-session*.target,
                it will be printed unless -q is given
                """
            ),
        )
        parsers["may_start"].add_argument(
            "vtnr",
            metavar="N",
            type=int,
            # default does not work here
            default=[1],
            nargs="*",
            help="VT numbers allowed for startup (default: 1).",
        )
        parsers["may_start_verbosity"] = parsers[
            "may_start"
        ].add_mutually_exclusive_group()
        parsers["may_start_verbosity"].add_argument(
            "-v", action="store_true", dest="verbose", help="Show all failed tests."
        )
        parsers["may_start_verbosity"].add_argument(
            "-q", action="store_true", dest="quiet", help="Do not show anything."
        )

        # aux subcommand
        parsers["aux"] = parsers["main_subparsers"].add_parser(
            "aux",
            formatter_class=HelpFormatterNewlines,
            help="Auxillary functions",
            description="Can only be called by systemd user manager, used in units Exec*= keys",
        )
        parsers["aux_subparsers"] = parsers["aux"].add_subparsers(
            title="Action subcommands",
            description=None,
            dest="aux_action",
            metavar="{subcommand}",
            required=True,
        )
        parsers["prepare_env"] = parsers["aux_subparsers"].add_parser(
            "prepare-env",
            formatter_class=HelpFormatterNewlines,
            help="Prepares environment (for use in wayland-wm-env@.service in wayland-session-pre@.target).",
            description="Used in ExecStart of wayland-wm-env@.service.",
            parents=[parsers["wm_args"]],
        )
        parsers["cleanup_env"] = parsers["aux_subparsers"].add_parser(
            "cleanup-env",
            formatter_class=HelpFormatterNewlines,
            help="Cleans up environment (for use in wayland-wm-env@.service in wayland-session-pre@.target).",
            description="Used in ExecStop of wayland-wm-env@.service.",
        )
        parsers["exec"] = parsers["aux_subparsers"].add_parser(
            "exec",
            formatter_class=HelpFormatterNewlines,
            help="Executes binary with arguments or desktop entry (for use in wayland-wm@.service in wayland-session@.target).",
            description="Used in ExecStart of wayland-wm@.service.",
        )
        parsers["exec"].add_argument(
            "wm_cmdline",
            nargs="+",
            metavar="wm",
            help="Executable or desktop entry (used as compositor ID), may be followed by arbitrary arguments.",
        )
        parsers["app_daemon"] = parsers["aux_subparsers"].add_parser(
            "app-daemon",
            formatter_class=HelpFormatterNewlines,
            help="Daemon for fast app argument generation",
            description="Receives app arguments from a named pipe, returns shell code",
            epilog=dedent(
                f"""
                Receives app arguments via "${{XDG_RUNTIME_DIR}}/uwsm-app-daemon-in" pipe.\n
                \n
                Arguments are expected to be "\\0"-delimited, leading "\\0" are stripped.
                One command is received per write+close.\n
                \n
                The first argument determines the behavior:\n
                \n
                  app	the rest is processed the same as in "{BIN_NAME} app"\n
                  ping	just "pong" is returned\n
                  stop	daemon is stopped\n
                \n
                Resulting arguments are formatted as shell code and written to
                "${{XDG_RUNTIME_DIR}}/uwsm-app-daemon-out" pipe.\n
                \n
                Single commands are prepended with "exec", iterated commands are assembled with trailing "&" each,
                followed by "wait"\n
                \n
                The purpose of all this is to skip all the expensive python startup and import routines that slow things
                down every time "{BIN_NAME} app" is called. Instead the daemon does it once and then listens for requests,
                while a simple shell script may dump arguments to one pipe and run the code received from another via eval,
                which is much faster\n
                \n
                The simplest script is:\n
                \n
                  #!/bin/sh\n
                  printf '\\0%s' app "$@" > ${{XDG_RUNTIME_DIR}}/uwsm-app-daemon-in\n
                  IFS='' read -r cmd < ${{XDG_RUNTIME_DIR}}/uwsm-app-daemon-out\n
                  eval "$cmd"\n
                \n
                Provided "{BIN_NAME}-app" client script is a bit smarter: it can start the daemon, applies timeouts,
                and supports newlines in returned args.
                """
            ),
        )

        if custom_args is None:
            # store args globally
            parsers["main"].parse_args(namespace=self.parsed)
            if store_parsers:
                self.parsers.__dict__.update(parsers)
        else:
            # store args in instance
            self.parsed = parsers["main"].parse_args(custom_args)
            if store_parsers:
                self.parsers = argparse.Namespace(**parsers)

    def __str__(self):
        return str({"parsed": self.parsed})


def finalize(additional_vars=None):
    """
    Exports variables to systemd and dbus activation environments,
    Sends service startup notification to systemd user manager to mark compositor service as active
    (if not already active).
    Optionally takes a list of additional vars to export.
    """

    print_debug("additional_vars", additional_vars)

    if additional_vars is None:
        additional_vars = []

    if not os.getenv("WAYLAND_DISPLAY", ""):
        print_error(
            "WAYLAND_DISPLAY is not defined or empty. Are we being run by a wayland compositor or not?"
        )
        sys.exit(1)
    export_vars = {}
    for var in ["WAYLAND_DISPLAY", "DISPLAY"] + sorted(additional_vars):
        value = os.getenv(var, None)
        if value is not None:
            export_vars.update({var: value})
    export_vars_names = sorted(export_vars.keys())

    # get id of active or activating compositor
    wm_id = get_active_wm_id()
    # get id ofactivating compositor for later decisions
    activating_wm_id = get_active_wm_id(active=False, activating=True)

    if not isinstance(wm_id, str) or not wm_id:
        print_error(
            "Finalization: Could not get ID of active or activating Wayland session. If it is in activating state, it will timeout in 10 seconds."
        )
        sys.exit(1)
    if activating_wm_id and wm_id != activating_wm_id:
        print_error(
            f'Finalization: Unit conflict, active: "{wm_id}", but another is activating: "{activating_wm_id}"!'
        )
        sys.exit(1)

    # append vars to cleanup file
    cleanup_file = os.path.join(
        BaseDirectory.get_runtime_dir(strict=True), f"env_names_for_cleanup_{wm_id}"
    )
    if os.path.isfile(cleanup_file):
        with open(cleanup_file, "r", encoding="UTF-8") as open_cleanup_file:
            current_cleanup_varnames = {
                l.strip() for l in open_cleanup_file.readlines() if l.strip()
            }
    else:
        print_error(f'"{cleanup_file}" does not exist!\nAssuming env preloader failed.')
        sys.exit(1)
    with open(cleanup_file, "w", encoding="UTF-8") as open_cleanup_file:
        open_cleanup_file.write(
            "\n".join(sorted(current_cleanup_varnames | set(export_vars_names)))
        )

    # export vars
    print_normal(
        "Exporting variables to systemd user manager:\n  "
        + "\n  ".join(export_vars_names)
    )

    try:
        set_systemd_vars(export_vars)
    except Exception as caught_exception:
        print_error_or_traceback(caught_exception)
        sys.exit(1)

    # if no prior failures and unit is in activating state, exec systemd-notify
    if activating_wm_id:
        print_normal(f"Finalizing startup of {wm_id}.")
        os.execlp("systemd-notify", "systemd-notify", "--ready")
    else:
        print_normal(f"Wayland session for {wm_id} is already active.")
        sys.exit(0)

    # we should not be here
    print_error("Something went wrong!")
    sys.exit(1)


def get_fg_vt():
    "Returns number of foreground VT or None"
    try:
        with open(
            "/sys/class/tty/tty0/active", "r", encoding="UTF-8"
        ) as active_tty_attr:
            fgvt = active_tty_attr.read()
        fgvt = fgvt.strip()
        if not fgvt.startswith("tty"):
            print_error(
                f'Reading "/sys/class/tty/tty0/active" returned "{fgvt}", expected "tty[0-9]"!'
            )
            return None
        fgvt_num = fgvt.removeprefix("tty")
        if not fgvt_num.isnumeric():
            print_error(
                f'Reading "/sys/class/tty/tty0/active" returned "{fgvt}", could not extract number!'
            )
            return None
        return int(fgvt_num)
    except Exception as caught_exception:
        print_error_or_traceback(caught_exception)
        return None


def get_session_by_vt(v_term: int, verbose: bool = False):
    "Takes VT number, returns associated XDG session ID or None"

    # get session list
    sprc = subprocess.run(
        ["loginctl", "list-sessions", "--no-legend", "--no-pager"],
        text=True,
        capture_output=True,
        check=False,
    )
    print_debug(sprc)
    if sprc.returncode != 0:
        if verbose:
            print_error(f'"{shlex.join(sprc.args)}" returned {sprc.returncode}!')
        return None
    if sprc.stderr.strip():
        print_error(sprc.stderr.strip())

    # iterate over sessions
    for line in sprc.stdout.splitlines():
        # id is the first alphanumeric in line, can be space-padded, so strip
        session_id = line.strip().split(" ")[0]
        print_debug("session_id", session_id)
        if not session_id:
            continue

        # get session user and VTNr
        sprc2 = subprocess.run(
            [
                "loginctl",
                "show-session",
                session_id,
                "--property",
                "Name",
                "--property",
                "VTNr",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        print_debug(sprc2)
        if sprc2.returncode != 0:
            if verbose:
                print_error(f'"{shlex.join(sprc2.args)}" returned {sprc2.returncode}!')
            continue
        if sprc2.stderr.strip():
            print_error(sprc.stderr.strip())

        # order is not governed by arguments, seems to be alphabetic, but sort to be sure
        props = sorted(sprc2.stdout.splitlines())
        if len(props) != 2:
            if verbose:
                print_error(
                    f'{shlex.join(sprc2.args)}" printed unparseable properties:\n{sprc2.stdout.strip()}!'
                )
            continue
        user: str = props[0].split("=")[1]
        vtnr: str = props[1].split("=")[1]

        if not user:
            if verbose:
                print_error(f'{shlex.join(sprc2.args)}" printed empty user!')
            continue
        if not vtnr.isnumeric():
            if verbose:
                print_error(
                    f'{shlex.join(sprc2.args)}" printed malformed vtnr: "{vtnr}"!'
                )
            continue

        if int(vtnr) == v_term and user == os.getlogin():
            return session_id

    return None


def prepare_env_gen_sh(random_mark):
    """
    Takes a known random string, returns string with shell code for sourcing env.
    Code echoes given string to mark the beginning of "env -0" output
    """

    # vars for use in plugins
    shell_definitions = dedent(
        f"""
        __SELF_NAME__={shlex.quote(BIN_NAME)}
        __WM_ID__={shlex.quote(CompGlobals.id)}
        __WM_ID_UNIT_STRING__={shlex.quote(CompGlobals.id_unit_string)}
        __WM_BIN_ID__={shlex.quote(CompGlobals.bin_id)}
        __WM_DESKTOP_NAMES__={shlex.quote(':'.join(CompGlobals.desktop_names))}
        __WM_FIRST_DESKTOP_NAME__={shlex.quote(CompGlobals.desktop_names[0])}
        __WM_DESKTOP_NAMES_EXCLUSIVE__={'true' if CompGlobals.cli_desktop_names_exclusive else 'false'}
        __OIFS__=" \t\n"
        """
    )

    # bake plugin loading into shell code
    shell_plugins = BaseDirectory.load_data_paths(
        f"uwsm/plugins/{CompGlobals.bin_id}.sh"
    )
    shell_plugins_load = []
    for plugin in shell_plugins:
        shell_plugins_load.append(
            dedent(
                f"""
                echo "Loading plugin \\"{plugin}\\""
                . "{plugin}"
                """
            )
        )
    shell_plugins_load = "".join(shell_plugins_load)

    # static part
    shell_main_body = dedent(
        r"""
        reverse() {
        	# returns list $1 delimited by ${2:-:} in reverese
        	__REVERSE_OUT__=''
        	IFS="${2:-:}"
        	for __ITEM__ in $1; do
        		if [ -n "${__ITEM__}" ]; then
        			__REVERSE_OUT__="${__ITEM__}${__REVERSE_OUT__:+$IFS}${__REVERSE_OUT__}"
        		fi
        	done
        	printf '%s' "${__REVERSE_OUT__}"
        	unset __REVERSE_OUT__
        	IFS="${__OIFS__}"
        }

        lowercase() {
        	# returns lowercase string
        	echo "$1" | tr '[:upper:]' '[:lower:]'
        }

        source_file() {
        	# sources file if exists, with messaging
        	if [ -f "${1}" ]; then
        		if [ -r "${1}" ]; then
        			echo "Loading environment from \"${1}\""
        			. "${1}"
        		else
        			"Environment file ${1} is not readable" >&2
        		fi
        	fi
        }

        get_all_config_dirs() {
        	# returns whole XDG_CONFIG hierarchy, :-delimited
        	printf '%s' "${XDG_CONFIG_HOME}:${XDG_CONFIG_DIRS}"
        }

        in_each_config_dir() {
        	# called for each config dir (decreasing priority)
        	true
        }

        in_each_config_dir_reversed() {
        	# called for each config dir in reverse (increasing priority)

        	# compose sequence of env files from lowercase desktop names in reverse
        	IFS=':'
        	__ENV_FILES__=''
        	for __DNLC__ in $(lowercase "$(reverse "${XDG_CURRENT_DESKTOP}")"); do
        		IFS="${__OIFS__}"
        		__ENV_FILES__="${__SELF_NAME__}-env-${__DNLC__}${__ENV_FILES__:+:}${__ENV_FILES__}"
        	done
        	# add common env file at the beginning
        	__ENV_FILES__="${__SELF_NAME__}-env${__ENV_FILES__:+:}${__ENV_FILES__}"
        	unset __DNLC__

        	# load env file sequence from this config dir rung
        	IFS=':'
        	for __ENV_FILE__ in ${__ENV_FILES__}; do
        		source_file "${1}/${__ENV_FILE__}"
        	done
        	unset __ENV_FILE__
        	unset __ENV_FILES__
        	IFS="${__OIFS__}"
        }

        process_config_dirs() {
        	# iterate over config dirs (decreasing importance) and call in_each_config_dir* functions
        	IFS=":"
        	for __CONFIG_DIR__ in $(get_all_config_dirs); do
        		IFS="${__OIFS__}"
        		if type "in_each_config_dir_${__WM_BIN_ID__}" >/dev/null 2>&1; then
        			"in_each_config_dir_${__WM_BIN_ID__}" "${__CONFIG_DIR__}" || return $?
        		else
        			in_each_config_dir "${__CONFIG_DIR__}" || return $?
        		fi
        	done
        	unset __CONFIG_DIR__
        	IFS="${__OIFS__}"
        	return 0
        }

        process_config_dirs_reversed() {
        	# iterate over reverse config dirs (increasing importance) and call in_each_config_dir_reversed* functions
        	IFS=":"
        	for __CONFIG_DIR__ in $(reverse "$(get_all_config_dirs)"); do
        		IFS="${__OIFS__}"
        		if type "in_each_config_dir_reversed_${__WM_BIN_ID__}" >/dev/null 2>&1; then
        			"in_each_config_dir_reversed_${__WM_BIN_ID__}" "${__CONFIG_DIR__}" || return $?
        		else
        			in_each_config_dir_reversed "${__CONFIG_DIR__}" || return $?
        		fi
        	done
        	unset __CONFIG_DIR__
        	IFS="${__OIFS__}"
        	return 0
        }

        load_wm_env() {
        	# calls reverse config dir processing
        	if type "process_config_dirs_reversed_${__WM_BIN_ID__}" >/dev/null 2>&1; then
        		"process_config_dirs_reversed_${__WM_BIN_ID__}" || return $?
        	else
        		process_config_dirs_reversed
        	fi
        }

        #### Basic environment
        [ -f /etc/profile ] && . /etc/profile
        [ -f "${HOME}/.profile" ] && . "${HOME}/.profile"
        export PATH
        export XDG_CONFIG_DIRS="${XDG_CONFIG_DIRS:-/etc/xdg}"
        export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
        export XDG_DATA_DIRS="${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
        export XDG_DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
        export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
        export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

        export XDG_CURRENT_DESKTOP="${__WM_DESKTOP_NAMES__}"
        export XDG_SESSION_DESKTOP="${__WM_FIRST_DESKTOP_NAME__}"
        export XDG_MENU_PREFIX="${__WM_FIRST_DESKTOP_NAME__}-"

        export XDG_SESSION_TYPE="wayland"

        #### apply quirks
        if type "quirks_${__WM_BIN_ID__}" >/dev/null 2>&1; then
        	echo "Applying quirks for \"${__WM_BIN_ID__}\""
        	"quirks_${__WM_BIN_ID__}" || exit $?
        fi

        if type "load_wm_env_${__WM_BIN_ID__}" >/dev/null 2>&1; then
        	"load_wm_env_${__WM_BIN_ID__}" || exit $?
        else
        	load_wm_env || exit $?
        	true
        fi
        """
    )

    # pass env after the mark
    shell_print_env = dedent(
        f"""
        printf "%s" "{random_mark}"
        env -0
        """
    )

    shell_full = "\n".join(
        [
            *(["set -x\n"] if int(os.getenv("DEBUG", "0")) > 0 else []),
            shell_definitions,
            shell_plugins_load,
            shell_main_body,
            *(["set +x\n"] if int(os.getenv("DEBUG", "0")) > 0 else []),
            shell_print_env,
        ]
    )

    return shell_full


def filter_varnames(data):
    """
    Filters variable names (some environments can introduce garbage).
    Accepts dicts of env or lists, tuples, sets of names. Returns only valid.
    """
    if not isinstance(data, (dict, set, list, tuple)):
        raise TypeError(f"Expected dict|set|list|tuple, received {type(data)}")

    if isinstance(data, dict):
        for var in list(data.keys()):
            if not Val.sh_varname.search(var):
                print_warning(f'Encountered illegal var "{var}".')
                data.pop(var)
        return data

    if isinstance(data, (set, list, tuple)):
        new_data = []
        for var in data:
            if not Val.sh_varname.search(var):
                print_warning(f'Encountered illegal var "{var}".')
            else:
                new_data.append(var)
        if isinstance(data, set):
            return set(new_data)
        if isinstance(data, tuple):
            return tuple(new_data)
        if isinstance(data, list):
            return new_data

    raise RuntimeError(f'Should not get here with data "{data}" ({type(data)})')


def prepare_env():
    """
    Runs shell code to source native shell env fragments,
    Captures difference in env before and after,
    Filters it and exports to systemd user manager,
    Saves list for later cleanup.
    """

    print_normal(
        f"Preparing environment for {CompGlobals.name or CompGlobals.cmdline[0]}..."
    )
    bus_session = DbusInteractions("session")
    print_debug("bus_session initial", bus_session)

    # get current ENV from systemd user manager
    # could use os.environ, but this is cleaner
    env_pre = filter_varnames(bus_session.get_systemd_vars())
    systemd_varnames = set(env_pre.keys())

    # override XDG_VTNR and XDG_SESSION_ID right away, they are in Varnames.always_export
    v_term = get_fg_vt()
    if v_term is None:
        raise RuntimeError("Could not determine foreground VT")
    session_id = get_session_by_vt(v_term)
    if session_id is None:
        raise RuntimeError("Could not determine session of foreground VT")
    env_pre.update({"XDG_VTNR": str(v_term), "XDG_SESSION_ID": session_id})

    # Run shell code with env_pre environment to prepare env and print results
    random_mark = f"MARK_{random_hex(16)}_MARK"
    shell_code = prepare_env_gen_sh(random_mark)

    sprc = subprocess.run(
        ["sh", "-"],
        text=True,
        input=shell_code,
        capture_output=True,
        env=env_pre,
        check=False,
    )
    print_debug(sprc)

    # cut everything before and including random mark, also the last \0
    # treat stdout before the mark as messages
    mark_position = sprc.stdout.find(random_mark)
    if mark_position < 0:
        # print whole stdout
        if sprc.stdout.strip():
            print_normal(sprc.stdout.strip())
        # print any stderr as errors
        if sprc.stderr.strip():
            print_error(sprc.stderr.strip())
        raise RuntimeError(
            f'Env output mark "{random_mark}" not found in shell output!'
        )

    stdout_msg = sprc.stdout[0:mark_position]
    stdout = sprc.stdout[mark_position + len(random_mark) :].rstrip("\0")

    # print stdout if any
    if stdout_msg.strip():
        print_normal(stdout_msg.strip())
    # print any stderr as errors
    if sprc.stderr.strip():
        print_error(sprc.stderr.strip())

    if sprc.returncode != 0:
        raise RuntimeError(f"Shell returned {sprc.returncode}!")

    # parse env
    env_post = {}
    for env in stdout.split("\0"):
        env = env.split("=", maxsplit=1)
        if len(env) == 2:
            env_post.update({env[0]: env[1]})
        else:
            print_error(f"No value: {env}!")
    env_post = filter_varnames(env_post)

    ## Dict of vars to put into systemd user manager
    # raw difference dict between env_post and env_pre
    set_env = dict(set(env_post.items()) - set(env_pre.items()))

    print_debug("env_pre", env_pre)
    print_debug("env_post", env_post)
    print_debug("set_env", set_env)

    # add "always_export" vars from env_post to set_env
    for var in sorted(
        Varnames.always_export - Varnames.never_export - Varnames.always_unset
    ):
        if var in env_post:
            print_debug(f'Forcing export of {var}="{env_post[var]}"')
            set_env.update({var: env_post[var]})

    # remove "never_export" and "always_unset" vars from set_env
    for var in Varnames.never_export | Varnames.always_unset:
        if var in set_env:
            print_debug(f"Excluding export of {var}")
            set_env.pop(var)

    # Set of varnames to remove from systemd user manager
    # raw reverse difference
    unset_varnames = set(env_pre.keys()) - set(env_post.keys())
    # add "always_unset" vars
    unset_varnames = unset_varnames | set(Varnames.always_unset)
    # leave only those that are defined in systemd user manager
    unset_varnames = unset_varnames & systemd_varnames

    # Set of vars to remove from systemd user manager on shutdown
    cleanup_varnames = (
        set(set_env.keys()) | Varnames.always_cleanup - Varnames.never_cleanup
    )

    # write cleanup file
    # first get exitsing vars if cleanup file already exists
    cleanup_file = os.path.join(
        BaseDirectory.get_runtime_dir(strict=True),
        f"env_names_for_cleanup_{CompGlobals.id}",
    )
    if os.path.isfile(cleanup_file):
        with open(cleanup_file, "r", encoding="UTF-8") as open_cleanup_file:
            current_cleanup_varnames = {
                l.strip() for l in open_cleanup_file.readlines() if l.strip()
            }
    else:
        current_cleanup_varnames = set()
    # write cleanup file
    with open(cleanup_file, "w", encoding="UTF-8") as open_cleanup_file:
        open_cleanup_file.write(
            "\n".join(sorted(current_cleanup_varnames | cleanup_varnames))
        )

    # print message about env export
    set_env_msg = "Exporting variables to systemd user manager:\n  " + "\n  ".join(
        sorted(set_env.keys())
    )
    print_normal(set_env_msg)
    # export env to systemd user manager
    set_systemd_vars(set_env)

    if unset_varnames:
        # print message about env unset
        unset_varnames_msg = (
            "Unsetting variables from systemd user manager:\n  "
            + "\n  ".join(sorted(unset_varnames))
        )
        print_normal(unset_varnames_msg)

        # unset env
        unset_systemd_vars(unset_varnames)

    # print message about future env cleanup
    cleanup_varnames_msg = (
        "Variables marked for cleanup from systemd user manager on stop:\n  "
        + "\n  ".join(sorted(cleanup_varnames))
    )
    print_normal(cleanup_varnames_msg)


def cleanup_env():
    """
    takes var names from "${XDG_RUNTIME_DIR}/env_names_for_cleanup_*"
    union Varnames.always_cleanup,
    difference Varnames.never_cleanup,
    intersect actual systemd user manager varnames,
    and remove them from systemd user manager.
    Remove found cleanup files
    Returns bool if cleaned up anything
    """

    print_normal("Cleaning up...")
    bus_session = DbusInteractions("session")
    print_debug("bus_session initial", bus_session)

    cleanup_file_dir = BaseDirectory.get_runtime_dir(strict=True)
    cleanup_files = []
    for cleanup_file in os.listdir(cleanup_file_dir):
        if not cleanup_file.startswith("env_names_for_cleanup_"):
            continue
        cleanup_file = os.path.join(cleanup_file_dir, cleanup_file)
        if os.path.isfile(cleanup_file):
            print_normal(f'Found cleanup_file "{os.path.basename(cleanup_file)}".')
            cleanup_files.append(cleanup_file)

    if not cleanup_files:
        print_warning("No cleanup files found.")
        return False

    current_cleanup_varnames = set()
    for cleanup_file in cleanup_files:
        if os.path.isfile(cleanup_file):
            with open(cleanup_file, "r", encoding="UTF-8") as open_cleanup_file:
                current_cleanup_varnames = current_cleanup_varnames | {
                    l.strip() for l in open_cleanup_file.readlines() if l.strip()
                }

    systemd_vars = bus_session.get_systemd_vars()
    systemd_varnames = set(systemd_vars.keys())

    cleanup_varnames = (
        current_cleanup_varnames
        | Varnames.always_cleanup - Varnames.never_cleanup & systemd_varnames
    )

    if cleanup_varnames:
        cleanup_varnames_msg = (
            "Cleaning up variables from systemd user manager:\n  "
            + "\n  ".join(sorted(cleanup_varnames))
        )
        print_normal(cleanup_varnames_msg)

        # unset vars
        unset_systemd_vars(cleanup_varnames)

    for cleanup_file in cleanup_files:
        os.remove(cleanup_file)
        print_ok(f'Removed "{os.path.basename(cleanup_file)}".')
    return True


def path2url(arg):
    "If argument is not an url, convert to url"
    if urlparse.urlparse(arg).scheme:
        return arg
    return f"file:{urlparse.quote(arg)}"


def gen_entry_args(entry, args, entry_action=None):
    """
    Takes DesktopEntry object and additional args, returns rendered argv as (cmd, args).
    "args" can be a list of args or a list of lists of args if multiple instances of cmd
    are required.
    """

    # Parsing of fields:
    # %f single path, run multiple instances per path
    # %F multiple paths as args
    # %u single url, convert non-url to 'file:' url, run multiple instances per url
    # %U multiple urls as args, convert non-url to 'file:' url
    # %c translated Name=
    # %k entry path
    # %i --icon getIcon() if getIcon()

    entry_dict = entry_action_keys(entry, entry_action=entry_action)
    entry_argv = shlex.split(entry_dict["Exec"])

    entry_cmd, entry_args = entry_argv[0], entry_argv[1:]
    print_debug("entry_cmd, entry_args pre:", entry_cmd, entry_args)

    ## search for fields to expand or pop
    # expansion counter
    expand = 0
    # %[fFuU] recorder
    encountered_fu = ""

    for idx, entry_arg in enumerate(entry_args.copy()):
        print_debug(f"parsing argument {idx + 1}: {entry_arg}")
        if entry_arg == "%f":
            if encountered_fu:
                raise RuntimeError(
                    f'Desktop entry has conflicting args: "{encountered_fu}", "{entry_arg}"'
                )
            encountered_fu = entry_arg

            if len(args) <= 1:
                # pop field arg
                entry_args.pop(idx + expand)
                expand -= 1
                print_debug(f"popped {entry_arg}, expand: {expand}, {entry_args}")

                if args:
                    # replace field with single argument
                    entry_args.insert(idx + expand, args[0])
                    expand += 1
                    print_debug(f"added {args[0]}, expand: {expand}, {entry_args}")

            else:
                # leave field arg for later iterative replacement
                print_debug(f"ignored {entry_arg}, expand: {expand}, {entry_args}")

        elif entry_arg == "%F":
            if encountered_fu:
                raise RuntimeError(
                    f'Desktop entry has conflicting args: "{encountered_fu}", "{entry_arg}"'
                )
            encountered_fu = entry_arg

            # pop field arg
            entry_args.pop(idx + expand)
            expand -= 1
            print_debug(f"popped {entry_arg}, expand: {expand}, {entry_args}")

            # replace with arguments
            for arg in args:
                entry_args.insert(idx + expand, arg)
                expand += 1
                print_debug(f"added {arg}, expand: {expand}, {entry_args}")

        elif entry_arg == "%u":
            if encountered_fu:
                raise RuntimeError(
                    f'Desktop entry has conflicting args: "{encountered_fu}", "{entry_arg}"'
                )
            encountered_fu = entry_arg

            if len(args) <= 1:
                # pop field arg
                entry_args.pop(idx + expand)
                expand -= 1
                print_debug(f"popped {entry_arg}, expand: {expand}, {entry_args}")

                if args:
                    # replace field with single argument
                    # convert to url, assume file
                    arg = path2url(args[0])
                    entry_args.insert(idx + expand, arg)
                    expand += 1
                    print_debug(f"added {arg}, expand: {expand}, {entry_args}")

            else:
                # leave field arg for later iterative replacement
                print_debug(f"ignored {entry_arg}, expand: {expand}, {entry_args}")

        elif entry_arg == "%U":
            if encountered_fu:
                raise RuntimeError(
                    f'Desktop entry has conflicting args: "{encountered_fu}", "{entry_arg}"'
                )
            encountered_fu = entry_arg

            # pop field arg
            entry_args.pop(idx + expand)
            expand -= 1
            print_debug(f"popped {entry_arg}, expand: {expand}, {entry_args}")

            # replace with arguments
            for arg in args:
                # urify if not an url, assume file
                arg = path2url(arg)
                entry_args.insert(idx + expand, arg)
                expand += 1
                print_debug(f"added {arg}, expand: {expand}, {entry_args}")

        elif entry_arg == "%c":
            entry_args[idx + expand] = entry.getName()
            print_debug(f"replaced, expand: {expand}. {entry_args}")
        elif entry_arg == "%k":
            entry_args[idx + expand] = entry.getFileName()
            print_debug(f"replaced, expand: {expand}. {entry_args}")
        elif entry_arg == "%i":
            if entry_dict["Icon"]:
                entry_args[idx + expand] = "--icon"
                entry_args.insert(idx + expand + 1, entry_dict["Icon"])
                expand += 1
                print_debug(f"replaced and expanded, expand: {expand}. {entry_args}")
            else:
                entry_args.pop(idx + expand)
                expand -= 1
                print_debug(f"popped, expand: {expand}")

    print_debug("entry_cmd, entry_args post:", entry_cmd, entry_args)

    # fail if arguments not supported, but requested
    if args and not encountered_fu and entry_action:
        raise RuntimeError(
            f'Entry "{os.path.basename(entry.filename)}" action "{entry_action}" does not support arguments'
        )

    if args and not encountered_fu and not entry_action:
        raise RuntimeError(
            f'Entry "{os.path.basename(entry.filename)}" does not support arguments'
        )

    # iterative arguments required
    if len(args) > 1 and encountered_fu in ["%f", "%u"]:
        iterated_entry_args = []
        field_index = entry_args.index(encountered_fu)
        for arg in args:
            cur_entry_args = entry_args.copy()
            if encountered_fu == "%u":
                arg = path2url(arg)
            cur_entry_args[field_index] = arg
            iterated_entry_args.append(cur_entry_args)
            print_debug("added iter args", cur_entry_args)

        return (entry_cmd, iterated_entry_args)

    return (entry_cmd, entry_args)


def find_terminal_entry():
    "Finds default terminal entry, returns tuple of (entry object, entry_id, entry_action) or (None, None, None)"

    terminal_entries = []

    ## read configs, compose preferred terminal entry list
    # iterate config dirs
    for config_dir in BaseDirectory.xdg_config_dirs:
        # iterate configs
        for config_file in [
            f"{desktop}-xdg-terminals.list"
            for desktop in sane_split(os.getenv("XDG_CURRENT_DESKTOP", ""), ":")
            if desktop
        ] + ["xdg-terminals.list"]:
            config_file = os.path.join(config_dir, config_file)
            try:
                with open(config_file, "r", encoding="UTF-8") as terminal_list:
                    print_debug(f"reading {config_file}")
                    for line in [line.strip() for line in terminal_list.readlines()]:
                        if not line or line.startswith("#"):
                            continue
                        arg = MainArg(line)
                        if (
                            arg.entry_id
                            and not arg.path
                            and (arg.entry_id, arg.entry_action) not in terminal_entries
                        ):
                            print_debug(f"got terminal entry {line}")
                            terminal_entries.append((arg.entry_id, arg.entry_action))
            except FileNotFoundError:
                pass
            except Exception as caught_exception:
                print_error_or_traceback(caught_exception)

    print_debug("explicit terminal_entries", terminal_entries)

    ## process explicitly listed terminals
    if terminal_entries:
        found_terminal_entries = find_entries(
            "applications",
            parser=entry_parser_terminal,
            parser_args={"explicit_terminals": terminal_entries},
        )
        print_debug(f"found {len(found_terminal_entries)} entries")
        # find first match in found terminals
        for entry_id, entry_action in terminal_entries:
            for (
                terminal_entry,
                terminal_entry_id,
                terminal_entry_action,
            ) in found_terminal_entries:
                if (entry_id, entry_action) == (
                    terminal_entry_id,
                    terminal_entry_action,
                ):
                    print_debug(f"found terminal {entry_id}:{entry_action}")
                    return (terminal_entry, terminal_entry_id, terminal_entry_action)

    print_debug("no explicit terminals matched, starting terminal entry search")

    Terminal.neg_cache = read_neg_cache("not-terminals")
    terminal_neg_cache_initial = Terminal.neg_cache.copy()

    # process all apps, find applicable terminal
    found_terminal_entries = find_entries(
        "applications", parser=entry_parser_terminal, reject_pmt=Terminal.neg_cache
    )
    if found_terminal_entries:
        terminal_entry, terminal_entry_id, _ = found_terminal_entries[0]
        print_debug(f"found terminal {terminal_entry_id}")
        if Terminal.neg_cache != terminal_neg_cache_initial:
            write_neg_cache("not-terminals", Terminal.neg_cache)
        return (terminal_entry, terminal_entry_id, None)

    raise RuntimeError("Could not find a Terminal Emulator application")


def read_neg_cache(name: str) -> dict:
    "Reads path;mtime from cache file {BIN_NAME}-{name}"
    neg_cache_path = os.path.join(BaseDirectory.xdg_cache_home, f"{BIN_NAME}-{name}")
    out = {}
    if os.path.isfile(neg_cache_path):
        print_debug(f"reading cache {neg_cache_path}")
        try:
            with open(neg_cache_path, "r", encoding="UTF-8") as neg_cache_file:
                for line in neg_cache_file.readlines():
                    path, mtime = line.strip().split(";")
                    out.update({path: float(mtime)})
        except Exception as caught_exception:
            # just remove it if something is wrong
            print_debug(
                f"Removing cahce file {neg_cache_path} due to: {caught_exception}"
            )
            os.remove(neg_cache_path)
    else:
        print_debug(f"no cache {neg_cache_path}")
    print_debug(f"got {len(out)} items")
    return out


def write_neg_cache(name: str, data: dict):
    "Writes path;mtime to cache file {BIN_NAME}-{name}"
    neg_cache_path = os.path.join(BaseDirectory.xdg_cache_home, f"{BIN_NAME}-{name}")
    print_debug(f"writing cache {neg_cache_path} ({len(data)} items)")
    try:
        if not os.path.isdir(BaseDirectory.xdg_cache_home):
            os.mkdir(BaseDirectory.xdg_cache_home)
        with open(neg_cache_path, "w", encoding="UTF-8") as neg_cache_file:
            for path, mtime in data.items():
                neg_cache_file.write(f"{path};{mtime}\n")
    except Exception as caught_exception:
        # just remove it if something is wrong
        print_debug(f"Removing cahce file {neg_cache_path} due to: {caught_exception}")
        if os.path.isfile(neg_cache_path):
            os.remove(neg_cache_path)


def app(
    cmdline,
    terminal,
    slice_name,
    app_unit_type,
    app_name,
    unit_name,
    unit_description,
    fork=False,
    return_cmdline=False,
):
    """
    Exec given command or desktop entry via systemd-run in specific slice.
    If return_cmdline: return cmdline as list
    If fork: return subprocess object.
    """

    # detect desktop entry, update cmdline, app_name
    # cmdline can be empty if terminal is requested with -T
    main_arg = MainArg(cmdline[0] if cmdline else None)

    print_debug("main_arg", main_arg)

    if main_arg.path:
        main_arg.check_path()

    if main_arg.entry_id:
        print_debug("main_arg:", main_arg)

        # if given as a path, try parsing and checking entry directly
        if main_arg.path:
            try:
                entry = DesktopEntry(main_arg.path)
            except:
                raise RuntimeError(
                    f'Failed to parse entry "{main_arg.entry_id}" from "{main_arg.path}"!'
                )
            check_entry_basic(entry, main_arg.entry_action)

        # find entry by id
        else:
            entries = find_entries(
                "applications",
                parser=entry_parser_by_ids,
                parser_args={
                    "match_entry_id": main_arg.entry_id,
                    "match_entry_action": main_arg.entry_action,
                },
            )

            print_debug("got entrires", entries)
            if not entries:
                raise FileNotFoundError(f'Deskop entry not found: "{cmdline[0]}"')

            entry = entries[0]

        # request terminal
        if entry.getTerminal():
            print_debug("entry requested a terminal")
            terminal = True

        # set app name to entry id without extension if no override
        if not app_name:
            app_name = os.path.splitext(main_arg.entry_id)[0]

        # get localized entry name for description if no override
        if not unit_description:
            unit_description = " - ".join(
                n for n in (entry.getName(), entry.getGenericName()) if n
            )

        # generate command and args according to entry
        cmd, cmd_args = gen_entry_args(
            entry, cmdline[1:], entry_action=main_arg.entry_action
        )

        # if cmd_args is a list of lists, iterative execution is required
        if cmd_args and isinstance(cmd_args[0], (list, tuple)):
            # drop unit_name if multiple instances required
            if unit_name:
                print_warning(
                    f'Dropping unit name "{unit_name}" because entry "{os.path.basename(entry.filename)}" requires multiple instances for given arguments.'
                )
                unit_name = ""

            # background processes container
            sub_apps = []
            # poll registry container
            sub_apps_rc = []
            # call forking self for each instance
            for args_instance in cmd_args:
                cmdline_instance = [cmd] + args_instance
                # launch app in background
                sub_apps.append(
                    app(
                        cmdline_instance,
                        terminal,
                        slice_name,
                        app_unit_type,
                        app_name,
                        unit_name,
                        unit_description,
                        fork=True,
                        return_cmdline=return_cmdline,
                    )
                )
                # add placeholder for rc
                sub_apps_rc.append(None)

            if return_cmdline:
                return sub_apps

            # function for map()
            def is_int(checkvar):
                "checks if given var is int"
                return isinstance(checkvar, int)

            # poll subprocesses until they are all finished
            while not all(map(is_int, sub_apps_rc)):
                for idx, sub_app in enumerate(sub_apps):
                    if not is_int(sub_apps_rc[idx]):
                        sub_apps_rc[idx] = sub_app.poll()
                        if is_int(sub_apps_rc[idx]):
                            proc_exit_msg = f'systemd-run for "{shlex.join(sub_app.args[sub_app.args.index("--") + 1:])}" returned {sub_apps_rc[idx]}.'
                            if sub_apps_rc[idx] == 0:
                                print_normal(proc_exit_msg)
                            else:
                                print_error(proc_exit_msg)
                time.sleep(0.1)

            # if there is any non-zero rc
            if any(sub_apps_rc):
                sys.exit(1)
            sys.exit(0)

        # for single exec just reassemble cmdline
        else:
            cmdline = [cmd] + cmd_args

    # end of Desktop entry parsing

    print_debug("cmdline", cmdline)

    if terminal:
        # Terminal.entry, Terminal.entry_action_id are global, so generate only once
        # no matter how many times app() is called for forks
        if not Terminal.entry:
            (
                Terminal.entry,
                Terminal.entry_id,
                Terminal.entry_action_id,
            ) = find_terminal_entry()

        terminal_cmdline = shlex.split(
            entry_action_keys(Terminal.entry, Terminal.entry_action_id)["Exec"]
        )
        if Terminal.entry.hasKey("ExecArg"):
            terminal_execarg = Terminal.entry.get("ExecArg")
        elif Terminal.entry.hasKey("X-ExecArg"):
            terminal_execarg = Terminal.entry.get("X-ExecArg")
        else:
            terminal_execarg = "-e"
        terminal_execarg = [terminal_execarg] if terminal_execarg else []

        # discard explicit -e or execarg for terminal
        # only if follwed by something, otherwise it will error out on Command not found below
        if len(cmdline) > 1 and [cmdline[0]] in (terminal_execarg, ["-e"]):
            print_debug(f"discarded explicit terminal exec arg {cmdline[0]}")
            cmdline = cmdline[1:]

        # if -T is given and cmdline is empty or double terminated
        if cmdline in ([], ["--"]):
            if not app_name:
                app_name = os.path.splitext(Terminal.entry_id)[0]
            if not unit_description:
                unit_description = " - ".join(
                    n
                    for n in (Terminal.entry.getName(), Terminal.entry.getGenericName())
                    if n
                )
            # cmdline contents should not be referenced until the end of this function,
            # where it will be starred into nothingness
            cmdline = []
            # remove exec arg
            terminal_execarg = []
    else:
        terminal_cmdline, terminal_execarg, Terminal.entry_id = ([], [], "")

    if not unit_description:
        unit_description = (
            app_name or os.path.basename(cmdline[0])
            if cmdline
            else f"App launched by {BIN_NAME}"
        )

    if cmdline and not which(cmdline[0]):
        raise RuntimeError(f'Command not found: "{cmdline[0]}"')

    if slice_name == "a":
        slice_name = "app-graphical.slice"
    elif slice_name == "b":
        slice_name = "background-graphical.slice"
    elif slice_name == "s":
        slice_name = "session-graphical.slice"
    elif slice_name.endswith(".slice"):
        # slice_name = slice_name
        pass
    else:
        print_error(f"Invalid slice name: {slice_name}!")
        sys.exit(1)

    if not unit_name:
        # use first XDG_CURRENT_DESKTOP as part of scope name
        # use app command as part of scope name
        desktop_unit_substring = simple_systemd_escape(
            sane_split(os.getenv("XDG_CURRENT_DESKTOP", "uwsm"), ":")[0], start=False
        )
        cmd_unit_substring = simple_systemd_escape(
            app_name or os.path.basename(cmdline[0]), start=False
        )

        ## cut unit name to fit unit name in 255 chars
        # length of parts except cmd_unit_substring
        l_static = len("app---DEADBEEF.") + len(app_unit_type)
        l_all = l_static + len(desktop_unit_substring)
        # if other parts already halfway too long, this means desktop_unit_substring needs some trimming
        if l_all > 127:
            # reduce to 127
            l_check = l_static
            fragments = re.split(r"(\\x..)", desktop_unit_substring)
            desktop_unit_substring = ""
            for fragment in fragments:
                if len(fragment) + l_check < 127:
                    desktop_unit_substring = desktop_unit_substring + fragment
                    l_check += len(fragment)
                else:
                    if fragment.startswith(r"\x"):
                        break
                    desktop_unit_substring = (
                        desktop_unit_substring + fragment[0 : 127 - l_check]
                    )
                    break

        l_all = l_static + len(desktop_unit_substring) + len(cmd_unit_substring)

        # now cut cmd_unit_substring if too long
        if l_all > 255:
            # reduce to 255
            l_check = l_static + len(desktop_unit_substring)
            fragments = re.split(r"(\\x..)", cmd_unit_substring)
            cmd_unit_substring = ""
            for fragment in fragments:
                if len(fragment) + l_check < 255:
                    cmd_unit_substring = cmd_unit_substring + fragment
                    l_check += len(fragment)
                else:
                    if fragment.startswith(r"\x"):
                        break
                    cmd_unit_substring = (
                        cmd_unit_substring + fragment[0 : 255 - l_check]
                    )
                    break

        if app_unit_type == "scope":
            unit_name = f"app-{desktop_unit_substring}-{cmd_unit_substring}-{random_hex(8)}.{app_unit_type}"
        elif app_unit_type == "service":
            unit_name = f"app-{desktop_unit_substring}-{cmd_unit_substring}@{random_hex(8)}.{app_unit_type}"
        else:
            raise ValueError(f'Invalid app_unit_type "{app_unit_type}"')

    else:
        if not unit_name.endswith(f".{app_unit_type}"):
            raise ValueError(
                f'Only ".{app_unit_type}" is supported as unit suffix for {app_unit_type} unit type'
            )
        if len(unit_name) > 255:
            raise ValueError(
                f"Unit name is too long ({len(unit_name)} > 255): {unit_name}"
            )

    final_args = (
        "systemd-run",
        "--user",
        *(["--scope"] if app_unit_type == "scope" else ["--property=ExitType=cgroup"]),
        f"--slice={slice_name}",
        f"--unit={unit_name}",
        f"--description={unit_description}",
        "--quiet",
        "--collect",
        "--same-dir",
        "--",
        *(terminal_cmdline + terminal_execarg),
        *cmdline,
    )

    print_debug("final_args", *(final_args))

    if return_cmdline:
        return final_args

    if fork:
        return subprocess.Popen(final_args)

    os.execlp(final_args[0], *(final_args))


def app_daemon():
    """
    Listens for app arguments on uwsm-app-daemon-in fifo in runtime dir.
    Writes shell code to uwsm-app-daemon-out fifo.
    Expects receiving script to have functions "message", "error".
    """

    def trap_stopper(signal=0, stack_frame=None):
        """
        For use in signal trap to stop app daemon
        """
        print_normal(f"Received signal {signal}, stopping app daemon...")
        print_debug(stack_frame)
        # shutdown successfully
        sys.exit()

    signal.signal(signal.SIGINT, trap_stopper)
    signal.signal(signal.SIGTERM, trap_stopper)
    signal.signal(signal.SIGHUP, trap_stopper)

    # argparse exit_on_error is faulty https://github.com/python/cpython/issues/103498
    # crudely work around it
    error_flag_path = os.path.join(
        BaseDirectory.get_runtime_dir(strict=True), "uwsm-app-daemon-error"
    )

    def send_cmdline(args_in: List, args_out: str):
        "Takes original args_in (list), and final args_out (str), writes to output fifo"
        print_normal(f"received: {shlex.join(args_in)}\nsent: {args_out}")
        fifo_out_path = create_fifo("uwsm-app-daemon-out")
        with open(fifo_out_path, "w", encoding="UTF-8") as fifo_out:
            fifo_out.write(f"{args_out}\n")

    while True:
        # create both pipes right away and make sure they always exist
        fifo_in_path = create_fifo("uwsm-app-daemon-in")
        fifo_out_path = create_fifo("uwsm-app-daemon-out")

        # argparse exit workaround: read previous wrong args and send error message
        if os.path.isfile(error_flag_path):
            print_normal(f"error flag {error_flag_path} exists")
            with open(error_flag_path, "r", encoding="UTF-8") as error_file:
                old_args = error_file.read().lstrip("\0")
            os.remove(error_flag_path)
            old_args = sane_split(old_args, "\0")
            send_cmdline(
                old_args,
                f"error {shlex.quote('Invalid arguments: ' + shlex.join(old_args))} 2",
            )
            continue

        print_debug("reading command...")
        with open(fifo_in_path, "r", encoding="UTF-8") as fifo_in:
            line = fifo_in.read().lstrip("\0")

        args_in = sane_split(line, "\0")

        print_debug("args_in", args_in)

        # this will be written to fifo_out with trailing newline
        args_out = ""

        if len(args_in) == 0:
            send_cmdline(args_in, "error 'No args given!' 2")
            continue
        if args_in[0] == "stop":
            send_cmdline(args_in, "message 'Stopping app daemon.'")
            print_normal("Exiting.")
            sys.exit(0)
        if args_in[0] == "ping":
            send_cmdline(args_in, "pong")
            continue
        if args_in[0] != "app":
            send_cmdline(
                args_in,
                f"error {shlex.quote('Invalid arguments: ' + shlex.join(args_in))} 2",
            )
            continue

        # argparse exit workaround: write command as error flag file
        with open(error_flag_path, "w", encoding="UTF-8") as error_file:
            print_debug(f"writing {error_flag_path} in case of argparse exit")
            error_file.write(line.strip())

        # parse args via standard parser
        try:
            args = Args(args_in, exit_on_error=False)
        except Exception as caught_exception:
            send_cmdline(
                args_in,
                f"error {shlex.quote('Invalid arguments: ' + str(caught_exception))} 2",
            )
            continue

        # remove error flag file since args are parsed successfully
        print_debug(f"removing {error_flag_path}")
        os.remove(error_flag_path)

        # reset terminal entry
        Terminal.entry = None
        Terminal.entry_action_id = ""
        Terminal.entry_id = ""

        # call app with return_cmdline=True
        try:
            app_args = app(
                cmdline=args.parsed.cmdline,
                terminal=args.parsed.terminal,
                slice_name=args.parsed.slice_name,
                app_unit_type=args.parsed.app_unit_type,
                app_name=args.parsed.app_name,
                unit_name=args.parsed.unit_name,
                unit_description=args.parsed.unit_description,
                return_cmdline=True,
            )
            if isinstance(app_args[0], str):
                print_debug("got single command")
                args_out = f"exec {shlex.join(app_args)}"
            elif isinstance(app_args[0], (list, tuple)):
                print_debug("got iterated command")
                args_out = []
                for iter_app_args in app_args:
                    args_out.append(f"{shlex.join(iter_app_args)} &")
                args_out.append("wait")
                args_out = " ".join(args_out)
            send_cmdline(args_in, args_out)
        except Exception as caught_exception:
            send_cmdline(
                args_in, f"error {shlex.quote('Error: ' + str(caught_exception))} 1"
            )
            continue


def create_fifo(path):
    "Ensures path in runtime dir is fifo, returns full path"
    fifo_path = os.path.join(BaseDirectory.get_runtime_dir(strict=True), path)

    if os.path.exists(fifo_path):
        if stat.S_ISFIFO(os.stat(fifo_path).st_mode):
            print_debug(f"fifo {fifo_path} already exists.")
            return fifo_path
        print_debug(f"not a fifo: {fifo_path}, removing.")
        os.remove(fifo_path)
    print_debug(f"creating fifo {fifo_path}.")
    os.mkfifo(fifo_path)
    return fifo_path


def fill_wm_globals():
    """
    Fills vars in CompGlobals:
      cmdline
      cli_args
      id
      id_unit_string
      bin_id
      desktop_names
      cli_desktop_names
      cli_desktop_names_exclusive
      name
      cli_name
      description
      cli_description
    based on args or desktop entry
    """

    CompGlobals.id = Args.parsed.wm_cmdline[0]

    if not CompGlobals.id:
        print_error("Compositor is not provided!")
        Args.parsers.start.print_help(file=sys.stderr)
        sys.exit(1)

    # detect and parse desktop entry
    main_arg = MainArg(CompGlobals.id)
    if main_arg.path:
        print_error(
            f'Paths are not supported, only names or IDs, got: "{CompGlobals.id}"!'
        )
        Args.parsers.start.print_help(file=sys.stderr)
        sys.exit(1)

    if not Val.wm_id.search(CompGlobals.id):
        print_error(
            f'"{CompGlobals.id}" does not conform to "^[a-zA-Z0-9_.-]+$" pattern!'
        )
        sys.exit(1)

    # escape CompGlobals.id for systemd
    CompGlobals.id_unit_string = simple_systemd_escape(CompGlobals.id, start=False)

    if main_arg.entry_id:
        print_debug(f"Compositor ID is a desktop entry: {CompGlobals.id}")

        # find and parse entry
        entries = find_entries(
            "wayland-sessions",
            parser=entry_parser_by_ids,
            parser_args={
                "match_entry_id": main_arg.entry_id,
                "match_entry_action": main_arg.entry_action,
            },
        )
        if not entries:
            raise FileNotFoundError(f'Could not find entry "{CompGlobals.id}"')

        entry = entries[0]

        print_debug("entry", entry)

        entry_dict = entry_action_keys(entry, entry_action=main_arg.entry_action)

        # get Exec from entry as CompGlobals.cmdline
        CompGlobals.cmdline = shlex.split(entry_dict["Exec"])

        print_debug(
            f"self_name: {BIN_NAME}\nwm_cmdline[0]: {os.path.basename(CompGlobals.cmdline[0])}"
        )
        # if desktop entry uses us, deal with the other self.
        entry_uwsm_args = None
        if os.path.basename(CompGlobals.cmdline[0]) == BIN_NAME:
            try:
                if (
                    "start" not in CompGlobals.cmdline
                    or CompGlobals.cmdline[1] != "start"
                ):
                    raise ValueError(
                        f'Entry "{CompGlobals.id}" uses {BIN_NAME}, but the second argument "{CompGlobals.cmdline[1]}" is not "start"!'
                    )
                # cut ourselves from cmdline
                CompGlobals.cmdline = CompGlobals.cmdline[1:]

                print_normal(
                    f'Entry "{CompGlobals.id}" uses {BIN_NAME}, reparsing args...'
                )
                # reparse args from entry into separate namespace
                entry_uwsm_args = Args(CompGlobals.cmdline)
                print_debug("entry_uwsm_args.parsed", entry_uwsm_args.parsed)

                # check for various incompatibilities
                if MainArg(entry_uwsm_args.parsed.wm_cmdline[0]).entry_id is not None:
                    raise ValueError(
                        f'Entry "{CompGlobals.id}" uses {BIN_NAME} that points to a desktop entry "{entry_uwsm_args.parsed.wm_cmdline[0]}"!'
                    )
                if entry_uwsm_args.parsed.dry_run:
                    raise ValueError(
                        f'Entry "{CompGlobals.id}" uses {BIN_NAME} in "dry run" mode!'
                    )
                if entry_uwsm_args.parsed.only_generate:
                    raise ValueError(
                        f'Entry "{CompGlobals.id}" uses {BIN_NAME} in "only generate" mode!'
                    )
                if entry_uwsm_args.parsed.desktop_names and not Val.dn_colon.search(
                    entry_uwsm_args.parsed.desktop_names
                ):
                    raise ValueError(
                        f'Entry "{CompGlobals.id}" uses {BIN_NAME} with malformed desktop names: "{entry_uwsm_args.parsed.desktop_names}"!'
                    )

                # replace CompGlobals.cmdline with Args.parsed.wm_cmdline from entry
                CompGlobals.cmdline = entry_uwsm_args.parsed.wm_cmdline

            except Exception as caught_exception:
                print_error_or_traceback(caught_exception)
                sys.exit(1)

        # combine Exec from entry and arguments
        # TODO: either drop this behavior, or add support for % fields
        # not that wayland session entries will ever use them
        CompGlobals.cmdline = CompGlobals.cmdline + Args.parsed.wm_cmdline[1:]

        print_debug("CompGlobals.cmdline", CompGlobals.cmdline)

        # use existence of Args.parsed.desktop_names as a condition
        # because this does not happen in aux exec mode
        if "desktop_names" in Args.parsed:
            # check desktop names
            if Args.parsed.desktop_names and not Val.dn_colon.search(
                Args.parsed.desktop_names
            ):
                print_error(
                    f'Got malformed desktop names: "{Args.parsed.desktop_names}"!'
                )
                sys.exit(1)

            # exclusive CLI desktop names
            if Args.parsed.desktop_names_exclusive:
                # error out on conflicting args
                if not Args.parsed.desktop_names:
                    print_error(
                        'Requested exclusive desktop names ("-e") but no desktop names were given via "-D"!'
                    )
                    sys.exit(1)
                else:
                    # set exclusive desktop names
                    CompGlobals.desktop_names = sane_split(
                        Args.parsed.desktop_names, ":"
                    )

            # exclusive nested CLI desktop names from entry
            elif (
                entry_uwsm_args is not None
                and entry_uwsm_args.parsed.desktop_names_exclusive
            ):
                if not entry_uwsm_args.parsed.desktop_names:
                    print_error(
                        f'{BIN_NAME} in entry "{CompGlobals.id}" requests exclusive desktop names ("-e") but has no desktop names listed via "-D"!'
                    )
                    sys.exit(1)
                else:
                    # set exclusive desktop names
                    CompGlobals.desktop_names = sane_split(
                        entry_uwsm_args.parsed.desktop_names, ":"
                    )
            # prepend desktop names from entry (and existing environment if there is no active session)
            # treating us processing an entry the same as us being launched by DM with XDG_CURRENT_DESKTOP
            # set by it from DesktopNames
            # basically just throw stuff into CompGlobals.desktop_names, deduplication comes later
            else:
                CompGlobals.desktop_names = (
                    (
                        sane_split(os.environ.get("XDG_CURRENT_DESKTOP", ""), ":")
                        if not is_active()
                        else []
                    )
                    + entry.get("DesktopNames", list=True)
                    + [CompGlobals.cmdline[0]]
                    + (
                        sane_split(entry_uwsm_args.parsed.desktop_names, ":")
                        if entry_uwsm_args is not None
                        else []
                    )
                    + sane_split(Args.parsed.desktop_names, ":")
                )
            print_debug("CompGlobals.desktop_names", CompGlobals.desktop_names)

            # fill name and description with fallbacks from: CLI, nested CLI, Entry
            if Args.parsed.wm_name:
                CompGlobals.name = Args.parsed.wm_name
            elif entry_uwsm_args is not None and entry_uwsm_args.parsed.wm_name:
                CompGlobals.name = entry_uwsm_args.parsed.wm_name
            else:
                CompGlobals.name = " - ".join(
                    n for n in (entry_dict["Name"], entry.getGenericName()) if n
                )

            if Args.parsed.wm_comment:
                CompGlobals.description = Args.parsed.wm_comment
            elif entry_uwsm_args is not None and entry_uwsm_args.parsed.wm_comment:
                CompGlobals.description = entry_uwsm_args.parsed.wm_comment
            elif entry.getComment():
                CompGlobals.description = entry.getComment()

    else:
        print_debug(f"Compositor ID is an executable: {CompGlobals.id}")

        # check exec
        if not which(CompGlobals.id):
            print_error(f'"{CompGlobals.id}" is not in PATH!')
            sys.exit(1)

        CompGlobals.cmdline = Args.parsed.wm_cmdline

        # this does not happen in aux exec mode
        if "desktop_names" in Args.parsed:
            # fill other data
            if Args.parsed.desktop_names_exclusive:
                CompGlobals.desktop_names = sane_split(Args.parsed.desktop_names, ":")
            else:
                CompGlobals.desktop_names = (
                    (
                        sane_split(os.environ.get("XDG_CURRENT_DESKTOP", ""), ":")
                        if not is_active()
                        else []
                    )
                    + [CompGlobals.cmdline[0]]
                    + sane_split(Args.parsed.desktop_names, ":")
                )
            CompGlobals.name = Args.parsed.wm_name
            CompGlobals.description = Args.parsed.wm_comment
            print_debug("CompGlobals.desktop_names", CompGlobals.desktop_names)

    # fill cli-exclusive vars for reproduction in unit drop-ins
    CompGlobals.cli_args = Args.parsed.wm_cmdline[1:]
    # this does not happen in aux exec mode
    if "desktop_names" in Args.parsed:
        CompGlobals.cli_desktop_names = sane_split(Args.parsed.desktop_names, ":")
        CompGlobals.cli_desktop_names_exclusive = Args.parsed.desktop_names_exclusive
        CompGlobals.cli_name = Args.parsed.wm_name
        CompGlobals.cli_description = Args.parsed.wm_comment

        # deduplicate desktop names preserving order
        ddn = []
        for desktop_name in CompGlobals.cli_desktop_names:
            if desktop_name not in ddn:
                ddn.append(desktop_name)
        CompGlobals.cli_desktop_names = ddn
        ddn = []
        for desktop_name in CompGlobals.desktop_names:
            if desktop_name not in ddn:
                ddn.append(desktop_name)
        CompGlobals.desktop_names = ddn

    # id for functions and env loading
    CompGlobals.bin_id = re.sub(
        "(^[^a-zA-Z]|[^a-zA-Z0-9_])+", "_", CompGlobals.cmdline[0]
    ).lower()

    return True


def stop_wm():
    "Stops compositor if active, returns True if stopped, False if was already inactive"

    print_normal("Stopping compositor...")

    bus_session = DbusInteractions("session")
    print_debug("bus_session initial", bus_session)

    # query systemd dbus for matching compositor units
    units = bus_session.list_units_by_patterns(
        ["active", "activating"], ["wayland-wm@*.service"]
    )
    # get only IDs
    units = [str(unit[0]) for unit in units]

    if not units:
        print_ok("Compositor is not running.")
        return False

    # this really shoud not happen
    if len(units) > 1:
        print_warning(f"Multiple compositor units found: {', '.join(units)}!")

    if Args.parsed.dry_run:
        print_normal(f"Will stop compositor {units[0]}.")
        return True

    print_normal(f"Found running compositor {units[0]}.")

    job = bus_session.stop_unit(units[0], "fail")

    # wait for job to be done
    while True:
        jobs = bus_session.list_systemd_jobs()
        if job not in [check_job[4] for check_job in jobs]:
            break
        time.sleep(0.1)
    print_ok("Sent stop job.")

    return True


def trap_stopper(signal=0, stack_frame=None, systemctl_rc=None):
    """
    For use in signal trap or after waiting systemctl exits.
    Ensures compositor is stopped and exits.
    """

    # initiate only once
    if Stopper.initiated:
        print_debug(
            f"stopper was already initiated, now invoked with ({signal}, {stack_frame}, {systemctl_rc})"
        )
        return
    Stopper.initiated = True

    if systemctl_rc is not None:
        if systemctl_rc == 0:
            print_normal("systemctl exited normally")
        elif systemctl_rc == -15:
            print_warning("systemctl was terminated")
        elif systemctl_rc < 0:
            print_error(f"systemctl was killed with signal {systemctl_rc * -1}!")
        elif systemctl_rc > 0:
            print_error(f"systemctl returned {systemctl_rc}!")
    else:
        print_normal(f"Received signal {signal}.")
        print_debug(stack_frame)

    try:
        stop_wm()
        stop_rc = 0
    except Exception as caught_exception:
        print_error_or_traceback(caught_exception)
        stop_rc = 1

    if (stop_rc or systemctl_rc) and (sys.stdout.isatty() or sys.stderr.isatty()):
        # Check if parent process is login.
        # If it is, sleep a bit to show messages before console is cleared.
        try:
            with open(
                f"/proc/{os.getppid()}/cmdline", "r", encoding="UTF-8"
            ) as ppcmdline:
                parent_cmdline = ppcmdline.read()
            parent_cmdline = parent_cmdline.strip().split("\0")
            print_debug(f"parent_pid: {os.getppid()}")
            print_debug(f"parent_cmdline: {parent_cmdline}")
            if os.path.basename(parent_cmdline[0]) == "login":
                print_warning("Will exit in 10 seconds...")
                time.sleep(10)
        except Exception as caught_exception:
            # no error is needed here
            print_debug("Could not determine parent process command")
            print_debug(caught_exception)

    print_normal("Exiting.")
    sys.exit(stop_rc if systemctl_rc is None else systemctl_rc)


def main():
    "UWSM main entrypoint"

    # parse args globally
    Args(store_parsers=True)

    print_debug("Args.parsed", Args.parsed)

    #### SELECT
    if Args.parsed.mode == "select":
        try:
            default_id = get_default_comp_entry()
            select_wm_id = select_comp_entry(default_id)
            if select_wm_id:
                if select_wm_id == default_id:
                    print_normal(f"Default compositor ID unchanged: {select_wm_id}.")
                else:
                    save_default_comp_entry(select_wm_id)
                sys.exit(0)
            else:
                print_warning("No compositor was selected.")
                sys.exit(1)
        except Exception as caught_exception:
            print_error_or_traceback(caught_exception)
            sys.exit(1)

    #### START
    elif Args.parsed.mode == "start":
        # Get ID from whiptail menu
        if Args.parsed.wm_cmdline[0] in ["select", "default"]:
            try:
                default_id = get_default_comp_entry()
                select_wm_id = select_comp_entry(
                    default_id, Args.parsed.wm_cmdline[0] == "default"
                )
                if select_wm_id:
                    if select_wm_id != default_id:
                        save_default_comp_entry(select_wm_id)
                    # update Args.parsed.wm_cmdline in place
                    Args.parsed.wm_cmdline = [select_wm_id]
                else:
                    print_error("No compositor was selected!")
                    sys.exit(1)
            except Exception as caught_exception:
                print_error_or_traceback(caught_exception)
                sys.exit(1)

        try:
            fill_wm_globals()
        except Exception as caught_exception:
            print_error_or_traceback(caught_exception)
            sys.exit(1)

        print_normal(
            dedent(
                f"""
                 Selected compositor ID: {CompGlobals.id}
                           Command Line: {shlex.join(CompGlobals.cmdline)}
                       Plugin/binary ID: {CompGlobals.bin_id}
                  Initial Desktop Names: {':'.join(CompGlobals.desktop_names)}
                                   Name: {CompGlobals.name}
                            Description: {CompGlobals.description}
                """
            )
        )

        if is_active(verbose_active=True):
            print_error("A compositor or graphical-session* target is already active!")
            if not Args.parsed.dry_run:
                sys.exit(1)
            else:
                print_ok("...but this is dry run, so the dream continues.")

        generate_units()

        if UnitsState.changed:
            reload_systemd()
        else:
            print_normal("Units unchanged.")

        if Args.parsed.only_generate:
            print_warning("Only unit creation was requested. Will not go further.")
            sys.exit(0)

        bus_system = DbusInteractions("system")
        print_debug("bus_system initial", bus_system)

        # query systemd dbus for active matching units
        units = bus_system.list_units_by_patterns(
            ["active", "activating"], ["graphical.target"]
        )
        if len(units) < 1:
            print_warning(
                dedent(
                    """
                    System has not reached graphical.target.
                    It might be a good idea to screen for this with a condition.
                    Will continue in 5 seconds...
                    """
                )
            )
            time.sleep(5)

        if Args.parsed.dry_run:
            print_normal(f"Will start {CompGlobals.id}...")
            print_warning("Dry Run Mode. Will not go further.")
            sys.exit(0)
        else:
            print_normal(
                f"Starting {CompGlobals.id} and waiting while it is running..."
            )

        # trap exit on INT TERM HUP
        signal.signal(signal.SIGINT, trap_stopper)
        signal.signal(signal.SIGTERM, trap_stopper)
        signal.signal(signal.SIGHUP, trap_stopper)

        # run start job via systemctl
        # this will wait until compositor is stopped
        sprc = subprocess.run(
            [
                "systemctl",
                "--user",
                "start",
                "--wait",
                f"wayland-wm@{CompGlobals.id_unit_string}.service",
            ],
            check=False,
        )
        print_debug(sprc)

        # reuse trap_stopper with signal 0 to report on ended systemctl
        trap_stopper(systemctl_rc=sprc.returncode)

    #### STOP
    elif Args.parsed.mode == "stop":
        try:
            stop_result = stop_wm()
            stop_rc = 0
        except Exception as caught_exception:
            print_error_or_traceback(caught_exception)
            stop_result = False
            stop_rc = 1

        # Args.parsed.remove_units is False when not given, None if given without argument
        if Args.parsed.remove_units is not False:
            remove_units(Args.parsed.remove_units)
            if UnitsState.changed:
                reload_systemd()
            else:
                print_normal("Units unchanged.")

        sys.exit(stop_rc)

    #### FINALIZE
    elif Args.parsed.mode == "finalize":
        finalize(Args.parsed.env_names)

    #### APP
    elif Args.parsed.mode == "app":
        try:
            app(
                cmdline=Args.parsed.cmdline,
                terminal=Args.parsed.terminal,
                slice_name=Args.parsed.slice_name,
                app_unit_type=Args.parsed.app_unit_type,
                app_name=Args.parsed.app_name,
                unit_name=Args.parsed.unit_name,
                unit_description=Args.parsed.unit_description,
            )
        except Exception as caught_exception:
            print_error_or_traceback(caught_exception)
            sys.exit(1)

    #### CHECK
    elif Args.parsed.mode == "check" and Args.parsed.checker == "is-active":
        if is_active(Args.parsed.wm, Args.parsed.verbose):
            sys.exit(0)
        else:
            sys.exit(1)

    elif Args.parsed.mode == "check" and Args.parsed.checker == "may-start":
        already_active_msg = (
            "A compositor and/or graphical-session* targets are already active"
        )
        dealbreakers = []
        if is_active():
            dealbreakers.append(already_active_msg)

        # check if parent process is a login shell
        try:
            with open(
                f"/proc/{os.getppid()}/cmdline", "r", encoding="UTF-8"
            ) as ppcmdline:
                parent_cmdline = ppcmdline.read()
                parent_cmdline = parent_cmdline.strip()
            print_debug(f"parent_pid: {os.getppid()}")
            print_debug(f"parent_cmdline: {parent_cmdline}")
        except Exception as caught_exception:
            print_error("Could not determine parent process command!")
            print_error_or_traceback(caught_exception)
            sys.exit(1)
        if not parent_cmdline.startswith("-"):
            dealbreakers.append("Not in login shell")

        # check foreground VT
        fgvt = get_fg_vt()
        if fgvt is None:
            dealbreakers.append("Could not determine foreground VT")
        else:
            # argparse does not pass default for this
            allowed_vtnr = Args.parsed.vtnr or [1]
            if fgvt not in allowed_vtnr:
                dealbreakers.append(
                    f"Foreground VT ({fgvt}) is not among allowed VTs ({'|'.join([str(v) for v in allowed_vtnr])})"
                )

        # check for graphical target
        bus_system = DbusInteractions("system")
        print_debug("bus_system initial", bus_system)
        units = bus_system.list_units_by_patterns(
            ["active", "activating"], ["graphical.target"]
        )
        print_debug("graphical.target units", units)
        if len(units) < 1:
            dealbreakers.append("System has not reached graphical.target")

        if dealbreakers:
            if Args.parsed.verbose or (
                # if the only failed condition is active graphical session, say it,
                # unless -q is given
                not Args.parsed.quiet
                and dealbreakers == [already_active_msg]
            ):
                print_warning("\n  ".join(["May not start compositor:"] + dealbreakers))
            sys.exit(1)
        else:
            if Args.parsed.verbose:
                print_ok("May start compositor.")
            sys.exit(0)

    #### AUX
    elif Args.parsed.mode == "aux":
        manager_pid = int(os.getenv("MANAGERPID", ""))
        ppid = int(os.getppid())
        print_debug(f"manager_pid: {manager_pid}, ppid: {ppid}")
        if not manager_pid or manager_pid != ppid:
            print_error("Aux actions can only be run by systemd user manager!")
            sys.exit(1)

        if Args.parsed.aux_action == "prepare-env":
            fill_wm_globals()
            try:
                prepare_env()
                sys.exit(0)
            except Exception as caught_exception:
                print_error_or_traceback(caught_exception)
                try:
                    cleanup_env()
                except Exception as caught_exception:
                    print_error_or_traceback(caught_exception)
                sys.exit(1)
        elif Args.parsed.aux_action == "cleanup-env":
            if is_active("compositor-only", verbose_active=True):
                print_error("A compositor is running, will not cleanup environment!")
                sys.exit(1)
            else:
                try:
                    cleanup_env()
                    sys.exit(0)
                except Exception as caught_exception:
                    print_error_or_traceback(caught_exception)
                    sys.exit(1)
        elif Args.parsed.aux_action == "exec":
            fill_wm_globals()
            print_debug(CompGlobals.cmdline)
            print_normal(f"Starting: {shlex.join(CompGlobals.cmdline)}...")
            os.execlp(CompGlobals.cmdline[0], *(CompGlobals.cmdline))

        elif Args.parsed.aux_action == "app-daemon":
            print_normal("Launching app daemon", file=sys.stderr)
            try:
                app_daemon()
            except Exception as caught_exception:
                print_error_or_traceback(caught_exception)
                sys.exit(1)
