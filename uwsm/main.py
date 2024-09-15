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
import time
import signal
import traceback
import stat
from typing import List, Callable
from urllib import parse as urlparse
from select import select

from xdg import BaseDirectory
from xdg.util import which
from xdg.DesktopEntry import DesktopEntry
from xdg.Exceptions import ValidationError

from uwsm.params import *
from uwsm.misc import *
from uwsm.dbus import DbusInteractions


class CompGlobals:
    "Compositor global vars"
    # Full final compositor cmdline
    cmdline: List[str] = []
    # Compositor arguments that were given on CLI (without arg 0)
    cli_args: List[str] = []
    # Internal compositor ID (basename of the arg 0)
    id: str = None
    # escaped id string for unit specifier
    id_unit_string: str = None
    # basename of cmdline[0]
    bin_name: str = None
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


class UnitsState:
    "Holds flag to mark changes in systemd units"
    changed: bool = False


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
                            f'Invalid Desktop Entry Action "{entry_action}"'
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
                raise ValueError(f'Invalid Desktop Entry ID "{self.entry_id}"')

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

    def check_exec(self):
        "Checks if executable is reachable and executable"
        if self.executable is None:
            raise ValueError(f"Argument is not an executable: {self}")
        if not which(self.executable):
            raise FileNotFoundError(
                f'"{self.executable}" not found or is not executable!'
            )

    def check_path(self):
        "Checks if path exists and has appropriate permissions."
        if self.path is None:
            raise ValueError(f"Argument is not a path: {self}")
        if not os.path.isfile(self.path):
            raise FileNotFoundError(f'Path "{self.path}" does not exist!')
        if not os.access(self.path, os.R_OK):
            raise PermissionError(f'Path "{self.path}" is not readable!')
        if self.executable and not os.access(self.path, os.X_OK):
            raise PermissionError(f'Path "{self.path}" is not executable!')
        print_debug(f"Path {self.path} OK")


def entry_action_keys(entry, entry_action=None):
    "Extracts Name, Exec, Icon from entry with or without entry_action, returns as dict"
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
        if Val.invalid_locale_key_error.match(error):
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
    except Exception:
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
    except Exception as caught_exception:
        raise RuntimeError(
            f'Failed to parse entry "{match_entry_id}" from "{entry_path}"'
        ) from caught_exception

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
    except Exception:
        print_debug("failed to parse entry")
        Terminal.neg_cache.update({entry_path: os.path.getmtime(entry_path)})
        return ("drop", (None, None, None))

    # quick fail
    try:
        if "TerminalEmulator" not in entry.getCategories():
            print_debug("not a TerminalEmulator")
            Terminal.neg_cache.update({entry_path: os.path.getmtime(entry_path)})
            return ("drop", (None, None, None))
    except Exception:
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
    reject_ids: List[str] = None,
):
    """
    Takes data hierarchy subpath and optional arg parser
    If parser is callable, it is called for each found entry with (entry_id, entry_path)
    Return is expected to be (action, data)
    action: what to do with the data: append|extend|return|drop(or anything else)
    By default returns list of tuples [(entry_id, entry_path)],
    otherwise returns whatever parser tells in a list.
    reject_pmt is a mapping of path to mtime for quick rejection
    reject_id is a list of entry ids for quick rejection
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
                    print_debug(f"rejected {entry_path} by path to mtime mapping")
                    continue

                # get proper entry id relative to data_dir with path delimiters replaced by '-'
                entry_id = os.path.relpath(entry_path, data_dir).replace("/", "-")

                # quick reject on reject_ids list
                if reject_ids and entry_id in reject_ids:
                    print_debug(f"rejected {entry_path} by id list")
                    continue

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
    "Gets compositor Desktop Entry ID from {BIN_NAME}/default-id file in config hierarchy and fallback system data hiearchy"
    # TRANSITION: move config to subdir
    # iterate over config paths + system data paths
    extended_dirs = []
    for config_dir in BaseDirectory.load_config_paths(""):
        if os.path.isdir(config_dir):
            extended_dirs.append(config_dir)
    for config_dir in BaseDirectory.load_data_paths(""):
        # skip one in XDG_DATA_HOME
        if os.path.normpath(config_dir).startswith(
            os.path.normpath(BaseDirectory.xdg_data_home)
        ):
            continue
        if os.path.isdir(config_dir):
            extended_dirs.append(config_dir)

    for config_dir in extended_dirs:
        old_config_file = os.path.join(config_dir, f"{BIN_NAME}-default-id")
        config_file = os.path.join(config_dir, f"{BIN_NAME}/default-id")
        if os.path.isfile(old_config_file):
            if os.path.isfile(config_file):
                print_warning(
                    f'Encountered legacy config file "{old_config_file}" (ignored)!'
                )
                print_normal("Continuing in 5 seconds...")
                time.sleep(5)
            # fallback to legacy if no new config
            else:
                print_warning(f'Using legacy config file "{old_config_file}"!')
                print_normal("Continuing in 5 seconds...")
                time.sleep(5)
                config_file = old_config_file

        if os.path.isfile(config_file):
            try:
                with open(config_file, "r", encoding="UTF-8") as config_file:
                    for line in config_file.readlines():
                        if line.strip():
                            return line.strip()
            except Exception as caught_exception:
                print_error(caught_exception)
                continue
    return ""


def save_default_comp_entry(default):
    "Saves compositor Desktop Entry ID to {BIN_NAME}/default-id file in config hierarchy"
    if "dry_run" not in Args.parsed or not Args.parsed.dry_run:
        config_file = os.path.join(
            BaseDirectory.xdg_config_home, BIN_NAME, "default-id"
        )
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w", encoding="UTF-8") as config:
            config.write(default + "\n")
            print_ok(f"Saved default compositor ID: {default}.")

        # TRANSITION: move config to subdir
        old_config = os.path.join(
            BaseDirectory.xdg_config_home, f"{BIN_NAME}-default-id"
        )
        if os.path.isfile(old_config):
            os.remove(old_config)
            print_warning(f'Removed legacy config "{old_config}"')
            print_normal("Continuing in 5 seconds...")
            time.sleep(5)
    else:
        print_ok(f"Would save default compositor ID: {default}.")


def select_comp_entry(default="", just_confirm=False):
    """
    Uses whiptail to select among "wayland-sessions" Desktop Entries.
    Takes a "default" to preselect, "just_confirm" flag to return a found default right away
    """

    choices_raw: List[tuple[str]] = []
    choices: List[str] = []

    # Fill choces list with (entry_id, description, comment) tuples.
    # Sort found entries by entry IDs without .desktop extension, to avoid
    # '[_-]' vs '.' sorting issues.
    for _, entry_id, entry in sorted(
        [
            (entry_id.removesuffix(".desktop"), entry_id, entry)
            for entry_id, entry in find_entries(
                "wayland-sessions", parser=entry_parser_session
            )
        ]
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
        description_length = max(description_length, len(choice[1]))

    # pretty format choices
    col_overhead = 10
    try:
        col = os.get_terminal_size().columns
    except OSError:
        print_warning("Could not get terminal width, assuming 128")
        col = 128
    except Exception as caught_exception:
        print_error(caught_exception)
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
        print_warning(f'Default "{default}" was not found in wayland-sessions.')
        default = ""

    # just spit out default if requested and found
    if default and just_confirm:
        for choice in choices[::2]:
            if choice == default:
                return choice

    # no default default here

    # fail on missing whiptail
    if not which("whiptail"):
        raise FileNotFoundError(
            '"whiptail" is not in PATH, "select" feature is not supported!'
        )

    # fail on noninteractive terminal
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
    # if it is not dbus-broker, also set dbus environment vars
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
        except Exception:
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
            for name, descr, _, _ in active_units:
                print_normal(f"  {name} ({descr})")
            if verbose:
                if inactive_units:
                    print_normal(f"{len(inactive_units)} inactive:")
                for name, descr, _, _ in inactive_units:
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
        for name, descr, _, _ in active_units:
            print_normal(f"    {name} ({descr})")
    if inactive_units:
        print_normal(f"  {len(inactive_units)} inactive:")
        for name, descr, _, _ in inactive_units:
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
            Documentation=man:uwsm(1) man:systemd.special(7)
            Requires=wayland-wm-env@%i.service
            BindsTo=graphical-session-pre.target
            Before=graphical-session-pre.target
            PropagatesStopTo=graphical-session-pre.target
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
            RefuseManualStart=yes
            RefuseManualStop=yes
            StopWhenUnneeded=yes
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
            Documentation=man:uwsm(1) man:systemd.special(7)
            Requires=wayland-session-pre@%i.target wayland-wm@%i.service
            Wants=wayland-session-waitenv.service wayland-session-xdg-autostart@%i.target
            After=graphical-session-pre.target
            BindsTo=graphical-session.target
            Before=graphical-session.target
            PropagatesStopTo=graphical-session.target
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
            StopWhenUnneeded=yes
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
            Documentation=man:uwsm(1) man:systemd.special(7)
            PartOf=graphical-session.target
            After=wayland-session@%i.target graphical-session.target
            BindsTo=xdg-desktop-autostart.target
            Before=xdg-desktop-autostart.target
            PropagatesStopTo=xdg-desktop-autostart.target
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
            StopWhenUnneeded=yes
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
            Documentation=man:uwsm(1) man:systemd.special(7)
            DefaultDependencies=no
            Conflicts=graphical-session-pre.target graphical-session.target xdg-desktop-autostart.target
            After=graphical-session-pre.target graphical-session.target xdg-desktop-autostart.target
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
            Documentation=man:uwsm(1)
            BindsTo=wayland-session-pre@%i.target
            Before=wayland-session-pre@%i.target graphical-session-pre.target
            PropagatesStopTo=wayland-session-pre@%i.target
            OnSuccess=wayland-session-shutdown.target
            OnSuccessJobMode=replace-irreversibly
            OnFailure=wayland-session-shutdown.target
            OnFailureJobMode=replace-irreversibly
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
            RefuseManualStart=yes
            RefuseManualStop=yes
            StopWhenUnneeded=yes
            CollectMode=inactive-or-failed
            [Service]
            Type=oneshot
            RemainAfterExit=yes
            ExecStart={BIN_PATH} aux prepare-env -- "%I"
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
            Documentation=man:uwsm(1)
            Requires=wayland-session-pre@%i.target
            BindsTo=wayland-session@%i.target
            Before=wayland-session@%i.target graphical-session.target
            PropagatesStopTo=wayland-session@%i.target graphical-session.target
            After=wayland-session-pre@%i.target graphical-session-pre.target
            OnSuccess=wayland-session-shutdown.target
            OnSuccessJobMode=replace-irreversibly
            OnFailure=wayland-session-shutdown.target
            OnFailureJobMode=replace-irreversibly
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
            CollectMode=inactive-or-failed
            [Service]
            # awaits for ready state notification from compositor's child
            # should be issued by '{BIN_NAME} finalize'
            Type=notify
            NotifyAccess=all
            ExecStart={BIN_PATH} aux exec -- %I
            Restart=no
            TimeoutStartSec=10
            TimeoutStopSec=10
            SyslogIdentifier={BIN_NAME}_%I
            Slice={wayland_wm_slice}
            """
        ),
    )
    update_unit(
        "wayland-session-waitenv.service",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=Wait for WAYLAND_DISPLAY and other variables
            Documentation=man:uwsm(1)
            Before=graphical-session.target
            After=graphical-session-pre.target
            CollectMode=inactive-or-failed
            OnFailure=wayland-session-shutdown.target
            OnFailureJobMode=replace-irreversibly
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
            CollectMode=inactive-or-failed
            [Service]
            Type=oneshot
            RemainAfterExit=no
            ExecStart={BIN_PATH} aux waitenv
            Restart=no
            TimeoutStartSec=12
            SyslogIdentifier={BIN_NAME}_waitenv
            Slice=background.slice
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
            Documentation=man:uwsm(1)
            PartOf=graphical-session.target
            After=graphical-session.target
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
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
    # for bindpid use lightweight waitpid binary if available,
    # otherwise use aux waitpid shim
    # Ensure that the binary can be found in the service file
    waitpid_path = which("waitpid")
    if waitpid_path:
        bindpid_cmd = f"{waitpid_path} -e"
    else:
        bindpid_cmd = f"{BIN_PATH} aux waitpid"
    update_unit(
        "wayland-session-bindpid@.service",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            Description=Bind graphical session to PID %i
            Documentation=man:uwsm(1)
            OnSuccess=wayland-session-shutdown.target
            OnSuccessJobMode=replace-irreversibly
            OnFailure=wayland-session-shutdown.target
            OnFailureJobMode=replace-irreversibly
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
            CollectMode=inactive-or-failed
            [Service]
            Type=exec
            ExecStart={bindpid_cmd} %i
            Restart=no
            SyslogIdentifier={BIN_NAME}_bindpid
            Slice=background.slice
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
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
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
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
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
            Conflicts=wayland-session-shutdown.target
            Before=wayland-session-shutdown.target
            """
        ),
    )

    # compositor-specific additions from cli or desktop entry via drop-ins
    # paths
    wm_specific_preloader = (
        f"wayland-wm-env@{CompGlobals.id_unit_string}.service.d/50_custom.conf"
    )
    wm_specific_service = (
        f"wayland-wm@{CompGlobals.id_unit_string}.service.d/50_custom.conf"
    )
    # initial data as lists for later joining
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

    # name or description is given
    if CompGlobals.name or CompGlobals.description:
        description_substring: str = ", ".join(
            (
                s
                for s in (
                    CompGlobals.name or CompGlobals.bin_name,
                    CompGlobals.description,
                )
                if s
            )
        )
        wm_specific_preloader_data.append(
            f"Description=Environment preloader for {description_substring}\n"
        )

        wm_specific_service_data.append(
            f"Description=Main service for {description_substring}\n"
        )

    # preloader exec needs desktop names, ID and the first argument
    preloader_exec_base = [BIN_PATH, "aux", "prepare-env"]
    preloader_exec = []
    # exclusive desktop names were given on command line
    if CompGlobals.cli_desktop_names_exclusive:
        preloader_exec.extend(["-eD", ":".join(CompGlobals.cli_desktop_names)])
    # desktop names differ from just executable name
    elif CompGlobals.desktop_names != [CompGlobals.bin_name]:
        preloader_exec.extend(["-D", ":".join(CompGlobals.desktop_names)])
    # finish preloader exec with ID...
    if preloader_exec or os.path.isabs(CompGlobals.cmdline[0]):
        preloader_exec.extend(["--", "%I"])
        # and absolute first argument
        if os.path.isabs(CompGlobals.cmdline[0]):
            preloader_exec.append(CompGlobals.cmdline[0])

        # append to string list
        wm_specific_preloader_data.append(
            dedent(
                f"""
                [Service]
                ExecStart=
                ExecStart={shlex.join(preloader_exec_base + preloader_exec)}\n
                """
            )
        )

    # service exec needs ID and command line
    service_exec_base = [BIN_PATH, "aux", "exec", "--", "%I"]
    service_exec = []

    # hardcode is requested or executable is given by path, hardcode the whole cmdline
    if os.path.isabs(CompGlobals.cmdline[0]):
        service_exec.extend(CompGlobals.cmdline)
    # append cli args with empty first argument
    elif CompGlobals.cli_args:
        service_exec.extend([""] + CompGlobals.cli_args)

    # append to string list
    if service_exec:
        wm_specific_service_data.append(
            dedent(
                f"""
                [Service]
                ExecStart=
                ExecStart={shlex.join(service_exec_base + service_exec)}\n
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

    ## tweaks
    update_unit(
        "app-@autostart.service.d/slice-tweak.conf",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            # make autostart apps stoppable/restartable by target
            PartOf=xdg-desktop-autostart.target
            After=xdg-desktop-autostart.target
            [Service]
            # also put them in special graphical app slice
            Slice=app-graphical.slice
            """
        ),
    )
    ## hotfix some portals
    # upstream fix pending
    update_unit(
        "xdg-desktop-portal-gtk.service.d/order-tweak.conf",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            PartOf=graphical-session.target
            After=graphical-session.target
            """
        ),
    )
    # with kde portal is's complicated
    update_unit(
        "plasma-xdg-desktop-portal-kde.service.d/order-tweak.conf",
        dedent(
            f"""
            # injected by {BIN_NAME}, do not edit
            [Unit]
            X-UWSM-ID=GENERIC
            PartOf=graphical-session.target
            After=graphical-session.target
            """
        ),
    )


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
            except Exception:
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
                See "{BIN_NAME} {{subcommand}} -h" for further help on each subcommand.\n
                \n
                See "man {BIN_NAME}" for more detailed info on integration and operation.\n
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

        # compositor arguments for reuse via parents
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
                  - Executable name\n
                  - Desktop Entry ID (optionally with ":"-delimited action ID)\n
                  - Special value "select" or "default"\n
                If given as path, hardcode mode is implied.\n
                """
            ),
        )

        parsers["wm_args_raw"] = argparse.ArgumentParser(
            add_help=False,
            formatter_class=HelpFormatterNewlines,
            exit_on_error=exit_on_error,
        )
        parsers["wm_args_raw"].add_argument(
            "wm_cmdline",
            metavar="args",
            nargs="*",
            help="Full Compositor command line.",
        )

        parsers["wm_id"] = argparse.ArgumentParser(
            add_help=False,
            formatter_class=HelpFormatterNewlines,
            exit_on_error=exit_on_error,
        )
        parsers["wm_id"].add_argument("wm_id", metavar="ID", help="Compositor ID.")

        parsers["wm_meta"] = argparse.ArgumentParser(
            add_help=False,
            formatter_class=HelpFormatterNewlines,
            exit_on_error=exit_on_error,
        )
        parsers["wm_meta"].add_argument(
            "-D",
            metavar="name[:name...]",
            dest="desktop_names",
            default="",
            help="Names to fill XDG_CURRENT_DESKTOP with (:-separated).\n\nExisting var content is a starting point if no active session is running.",
        )
        parsers["wm_meta_dn_exclusive"] = parsers[
            "wm_meta"
        ].add_mutually_exclusive_group()
        parsers["wm_meta_dn_exclusive"].add_argument(
            "-a",
            dest="desktop_names_exclusive",
            action="store_false",
            default=False,
            help="Append desktop names set by -D to other sources (default).",
        )
        parsers["wm_meta_dn_exclusive"].add_argument(
            "-e",
            dest="desktop_names_exclusive",
            action="store_true",
            default=False,
            help="Use desktop names set by -D exclusively, discard other sources.",
        )
        parsers["wm_meta"].add_argument(
            "-N",
            metavar="Name",
            dest="wm_name",
            default="",
            help="Fancy name for compositor (filled from Desktop Entry by default).",
        )
        parsers["wm_meta"].add_argument(
            "-C",
            metavar="Comment",
            dest="wm_comment",
            default="",
            help="Fancy description for compositor (filled from Desktop Entry by default).",
        )

        # select subcommand
        parsers["select"] = parsers["main_subparsers"].add_parser(
            "select",
            formatter_class=HelpFormatterNewlines,
            help="Select default compositor entry",
            description="Invokes whiptail menu for selecting wayland-sessions Desktop Entries.",
            epilog=dedent(
                f"""
                Entries are selected from "wayland-sessions" XDG data hierarchy.
                Default selection is read from first encountered "{BIN_NAME}/default-id" file in
                XDG Config hierarchy and system part of XDG Data hierarchy. When selected, choice is
                saved to user part of XDG Config hierarchy ("${{XDG_CONFIG_HOME}}/{BIN_NAME}/default-id").
                Nothing else is done.
                """
            ),
        )

        # start subcommand
        parsers["start"] = parsers["main_subparsers"].add_parser(
            "start",
            formatter_class=HelpFormatterNewlines,
            help="Start compositor",
            description="Generates units for given compositor command line or Desktop Entry and starts compositor.",
            parents=[parsers["wm_args"], parsers["wm_meta"]],
            epilog=dedent(
                f"""
                Compositor should finalize its startup by running this:\n
                \n
                  {BIN_NAME} finalize [VAR ...]\n
                \n
                Otherwise, the compositor's unit will terminate due to startup timeout.
                """
            ),
        )
        use_session_slice = os.getenv("UWSM_USE_SESSION_SLICE", None)
        if use_session_slice in ("true", "false"):
            use_session_slice = {"true": True, "false": False}[use_session_slice]
        elif use_session_slice is None:
            pass
        else:
            print_warning(
                f'invalid UWSM_USE_SESSION_SLICE value "{use_session_slice}" ignored, set to soft "false".'
            )
            use_session_slice = None
        parsers["start_slice"] = parsers["start"].add_mutually_exclusive_group()
        parsers["start_slice"].add_argument(
            "-S",
            action="store_true",
            dest="use_session_slice",
            default=use_session_slice,
            help=f"Launch compositor in session.slice{' (already preset by UWSM_USE_SESSION_SLICE env var)' if use_session_slice == True else ''}.",
        )
        parsers["start_slice"].add_argument(
            "-A",
            action="store_false",
            dest="use_session_slice",
            default=use_session_slice,
            help=f"Launch compositor in app.slice{' (already preset by UWSM_USE_SESSION_SLICE env var)' if use_session_slice == False else ' (default)' if use_session_slice is None else ''}.",
        )
        parsers["start"].add_argument(
            "-F",
            action="store_true",
            dest="hardcode",
            default=False,
            help="Hardcode resulting command line (with path) to unit drop-ins.",
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
            help="Dry run, do not write or start anything.",
        )

        # stop subcommand
        parsers["stop"] = parsers["main_subparsers"].add_parser(
            "stop",
            formatter_class=HelpFormatterNewlines,
            help="Stop compositor",
            description="Stops compositor and optionally removes generated units.",
        )
        parsers["stop"].add_argument(
            "-r",
            nargs="?",
            metavar="compositor",
            default=False,
            dest="remove_units",
            help="Also remove units (all or only compositor-specific).",
        )
        parsers["stop"].add_argument(
            "-n",
            action="store_true",
            dest="dry_run",
            help="Dry run, do not stop or remove anything.",
        )

        # finalize subcommand
        parsers["finalize"] = parsers["main_subparsers"].add_parser(
            "finalize",
            formatter_class=HelpFormatterNewlines,
            help="Export variables from compositor, notify systemd of unit startup.",
            description="For use inside compositor to export essential variables and complete compositor unit startup.",
            epilog=dedent(
                """
                Exports variables to systemd user manager: WAYLAND_DISPLAY, DISPLAY,
                and any optional variables mentioned by name as arguments, or listed
                whitespace-separated in UWSM_FINALIZE_VARNAMES environment var.\n
                \n
                Variables are also added to cleanup list to be unset during deactivation.\n
                \n
                If all is well, sends startup notification to systemd user manager,
                so compositor unit is considered started and graphical-session.target
                can be declared reached.
                """
            ),
        )
        parsers["finalize"].add_argument(
            "env_names",
            metavar="VAR_NAME",
            nargs="*",
            help="Additional vars to export.",
        )

        # app subcommand
        parsers["app"] = parsers["main_subparsers"].add_parser(
            "app",
            formatter_class=HelpFormatterNewlines,
            help="Application unit launcher",
            description="Launches application as a scope or service in specific slice.",
            epilog=dedent(
                """
                It is highly recommended to configure your compositor to launch apps
                via this command to fully utilize user-level systemd unit management.\n
                When this is done, compositor itself can be put in session.slice by adding
                "-S" to "start" subcommand, or setting this variable in the environment
                where "{BIN_NAME} start" is executed.:\n
                \n
                  UWSM_USE_SESSION_SLICE=true\n
                \n
                """
            ),
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
                  - Executable name or path\n
                  - Desktop Entry ID (with optional ":"-delimited action ID)\n
                  - Path to Desktop Entry file (with optional ":"-delimited action ID)\n
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
            help="Performs state checks",
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
            help="Checks for active compositor",
            description="Checks for specific compositor or graphical-session*.target in general in active or activating state",
        )
        parsers["is_active"].add_argument(
            "wm",
            nargs="?",
            help="Specify compositor by executable or Desktop Entry (without arguments).",
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
                """
                Conditions:\n
                  - Running from login shell\n
                  - System is at graphical.target\n
                  - User graphical-session*.target are not yet active\n
                  - Foreground VT is among allowed (default: 1)\n
                \n
                This command is essential for integrating startup into shell profile.
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
        parsers["may_start"].add_argument(
            "-g",
            type=int,
            dest="gst_seconds",
            metavar="S",
            default=60,
            help="Seconds to wait for graphical.target in queue (default: 60; 0 or less disables check).",
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
            parents=[parsers["wm_id"], parsers["wm_args_raw"], parsers["wm_meta"]],
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
            help="Executes binary with arguments or Desktop Entry (for use in wayland-wm@.service in wayland-session@.target).",
            description="Used in ExecStart of wayland-wm@.service.",
            parents=[parsers["wm_id"], parsers["wm_args_raw"]],
        )
        parsers["app_daemon"] = parsers["aux_subparsers"].add_parser(
            "app-daemon",
            formatter_class=HelpFormatterNewlines,
            help="Daemon for fast app argument generation",
            description="Receives app arguments from a named pipe, returns shell code",
            epilog=dedent(
                f"""
                Receives app arguments via "${{XDG_RUNTIME_DIR}}/uwsm-app-daemon-in" pipe.\n
                Returns shell code to "${{XDG_RUNTIME_DIR}}/uwsm-app-daemon-out" pipe.
                \n
                Arguments are expected to be "\\0"-delimited, leading "\\0" are stripped.
                One command is received per write+close.\n
                \n
                The first argument determines the behavior:\n
                \n
                  app	the rest is processed the same as in "{BIN_NAME} app"\n
                  ping	just "pong" is returned\n
                  stop	daemon is stopped\n
                """
            ),
        )
        parsers["waitpid"] = parsers["aux_subparsers"].add_parser(
            "waitpid",
            formatter_class=HelpFormatterNewlines,
            help="Waits for a PID to exit (for use in wayland-session-bindpid@.service).",
            description=(
                "Exits successfully when a process by given PID ends, or if it does not exist. "
                "Used in wayland-session-bindpid@.service as a shim for waitpid if it is unavailable."
            ),
        )
        parsers["waitpid"].add_argument(
            "pid",
            type=int,
            metavar="PID",
            help="PID to wait for",
        )
        parsers["waitenv"] = parsers["aux_subparsers"].add_parser(
            "waitenv",
            formatter_class=HelpFormatterNewlines,
            help="Waits for WAYLAND_DISPLAY (and optionally other vars) to appear in systemd user manager environment.",
            description=(
                "Exits successfully when WAYLAND_DISPLAY (and optionally other vars) appears in systemd user manager activation environment."
            ),
            epilog="Also waits for vars listed in whitespace-separated UWSM_WAIT_VARNAMES environment var.",
        )
        parsers["waitenv"].add_argument(
            "env_names",
            type=list,
            nargs="*",
            metavar="VAR_NAME",
            help="Names of additional variables to wait for",
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


def append_to_cleanup_file(wm_id, varnames, skip_always_cleanup=False, create=True):
    "Aappend varnames to cleanup file, expects wm_id, varnames, create (bool)"
    cleanup_file = os.path.join(
        BaseDirectory.get_runtime_dir(strict=True),
        BIN_NAME,
        f"env_cleanup_{wm_id}.list",
    )

    # do not bother with useless cleanups
    if skip_always_cleanup:
        varnames = filter_varnames(set(varnames) - Varnames.never_cleanup - Varnames.always_cleanup)
    else:
        varnames = filter_varnames(set(varnames) - Varnames.never_cleanup)
    if not varnames:
        print_debug("no varnames to write to cleanup file")
        return

    if not os.path.exists(cleanup_file):
        if not create:
            raise FileNotFoundError(f'"{cleanup_file}" does not exist!')
        print_debug(f'cleanup file "{cleanup_file}" does not exist')
        os.makedirs(os.path.dirname(cleanup_file), exist_ok=True)

        # write new file
        with open(cleanup_file, "w", encoding="UTF-8") as open_cleanup_file:
            open_cleanup_file.write("\n".join(sorted(varnames)) + "\n")

    elif not os.path.isfile(cleanup_file):
        raise OSError(f'"{cleanup_file}" is not a file!')

    else:
        # first read existing varnames
        with open(cleanup_file, "r", encoding="UTF-8") as open_cleanup_file:
            # read varnames in a set
            current_cleanup_varnames = filter_varnames(
                {l.strip() for l in open_cleanup_file.readlines() if l.strip()}
            )
        print_debug(f'cleanup file "{cleanup_file}", varnames', current_cleanup_varnames)

        # subtract existing
        varnames = sorted(varnames - current_cleanup_varnames)

        # append new
        if varnames:
            print_debug("appending to cleanup file", varnames)
            with open(cleanup_file, "a", encoding="UTF-8") as open_cleanup_file:
                open_cleanup_file.write("\n".join(sorted(varnames)) + "\n")

        else:
            print_debug("no new varnames to append to cleanup file")
            return

    # print message about future env cleanup
    cleanup_varnames_msg = (
        "Marking variables for later cleanup from systemd user manager on stop:\n  "
        + "\n  ".join(sorted(varnames))
    )
    print_normal(cleanup_varnames_msg)


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
        raise ValueError(
            "WAYLAND_DISPLAY is not defined or empty. Are we being run by a wayland compositor or not?"
        )
    export_vars = {}
    export_vars_names = []
    for var in ["WAYLAND_DISPLAY", "DISPLAY"] + sorted(set(additional_vars)):
        value = os.getenv(var, None)
        if value is not None and var not in export_vars_names:
            export_vars.update({var: value})
            export_vars_names.append(var)

    # get id of active or activating compositor
    wm_id = get_active_wm_id()
    # get id of activating compositor for later decisions
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
    try:
        append_to_cleanup_file(wm_id, export_vars_names, create=False)
    except FileNotFoundError as caught_exception:
        print_error(caught_exception, "Assuming env preloader failed.")
        sys.exit(1)

    # export vars
    print_normal(
        "Exporting variables to systemd user manager:\n  "
        + "\n  ".join(export_vars_names)
    )

    set_systemd_vars(export_vars)

    # if no prior failures and unit is in activating state, exec systemd-notify
    if activating_wm_id:
        print_normal(f"Declaring unit for {wm_id} ready.")
        os.execlp("systemd-notify", "systemd-notify", "--ready")
    else:
        print_normal(f"Unit for {wm_id} is already active.")
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
        print_error(caught_exception)
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
        # vars for plugins
        __SELF_NAME__={shlex.quote(BIN_NAME)}
        __WM_ID__={shlex.quote(CompGlobals.id)}
        __WM_ID_UNIT_STRING__={shlex.quote(CompGlobals.id_unit_string)}
        __WM_BIN_ID__={shlex.quote(CompGlobals.bin_id)}
        __WM_DESKTOP_NAMES__={shlex.quote(':'.join(CompGlobals.desktop_names))}
        __WM_FIRST_DESKTOP_NAME__={shlex.quote(CompGlobals.desktop_names[0])}
        __WM_DESKTOP_NAMES_EXCLUSIVE__={'true' if CompGlobals.cli_desktop_names_exclusive else 'false'}
        __OIFS__=" \t\n"
        # context marker for profile scripting
        IN_UWSM_ENV_PRELOADER=true
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

        get_all_config_dirs_extended() {
        	# returns whole XDG_CONFIG and system XDG_DATA hierarchies, :-delimited
        	printf '%s' "${XDG_CONFIG_HOME}:${XDG_CONFIG_DIRS}:${XDG_DATA_DIRS}"
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
        		# TRANSITION: move to subdir
        		if [ -f "${1}/${__SELF_NAME__}-env-${__DNLC__}" ]; then
        			if [ -f "${1}/${__SELF_NAME__}/env-${__DNLC__}" ]; then
        				echo "Encountered legacy env file \"${1}/${__SELF_NAME__}-env-${__DNLC__}\" (ignored)!" >&2
        				__ENV_FILES__="${__SELF_NAME__}/env-${__DNLC__}${__ENV_FILES__:+:}${__ENV_FILES__}"
        			else
        				echo "Encountered legacy env file \"${1}/${__SELF_NAME__}-env-${__DNLC__}\" (used)!" >&2
        				__ENV_FILES__="${__SELF_NAME__}-env-${__DNLC__}${__ENV_FILES__:+:}${__ENV_FILES__}"
        			fi
        		else
        			__ENV_FILES__="${__SELF_NAME__}/env-${__DNLC__}${__ENV_FILES__:+:}${__ENV_FILES__}"
        		fi
        	done
        	# add common env file at the beginning
        	# TRANSITION: move to subdir
        	if [ -f "${1}/${__SELF_NAME__}-env" ]; then
        		if [ -f "${1}/${__SELF_NAME__}/env" ]; then
        			echo "Encountered legacy env file \"${1}/${__SELF_NAME__}-env\" (ignored)!" >&2
        			__ENV_FILES__="${__SELF_NAME__}/env${__ENV_FILES__:+:}${__ENV_FILES__}"
        		else
        			echo "Encountered legacy env file \"${1}/${__SELF_NAME__}-env\" (used)!" >&2
        			__ENV_FILES__="${__SELF_NAME__}-env${__ENV_FILES__:+:}${__ENV_FILES__}"
        		fi
        	else
        		__ENV_FILES__="${__SELF_NAME__}/env${__ENV_FILES__:+:}${__ENV_FILES__}"
        	fi
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
        	for __CONFIG_DIR__ in $(get_all_config_dirs_extended); do
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
        	for __CONFIG_DIR__ in $(reverse "$(get_all_config_dirs_extended)"); do
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
        export XDG_BACKEND="wayland"

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
        exec env -0
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
        f"Preparing environment for {CompGlobals.name or CompGlobals.bin_name}..."
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

    sh_path = which("sh")
    if not sh_path:
        print_error(f'"sh" is not in PATH!')
        sys.exit(1)

    sprc = subprocess.run(
        [sh_path, "-"],
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
        (set(set_env.keys()) | Varnames.always_cleanup) - Varnames.never_cleanup
    )

    # write cleanup file
    # first get exitsing vars if cleanup file already exists
    append_to_cleanup_file(CompGlobals.id, cleanup_varnames, create=True)

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


def cleanup_env():
    """
    takes var names from "${XDG_RUNTIME_DIR}/uwsm/env_cleanup_*.list"
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

    cleanup_file_dir = os.path.join(
        BaseDirectory.get_runtime_dir(strict=True), BIN_NAME
    )
    cleanup_files = []

    if os.path.isdir(cleanup_file_dir):
        for cleanup_file in os.listdir(cleanup_file_dir):
            if not cleanup_file.startswith("env_cleanup_") or not cleanup_file.endswith(
                ".list"
            ):
                continue
            cleanup_file = os.path.join(cleanup_file_dir, cleanup_file)
            if os.path.isfile(cleanup_file):
                print_normal(f'Found cleanup file "{os.path.basename(cleanup_file)}".')
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
        ((current_cleanup_varnames
        | Varnames.always_cleanup) - Varnames.never_cleanup) & systemd_varnames
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
    excluded_terminal_entries = []
    unexcluded_terminal_entries = []

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
                        if line.startswith("-"):
                            fbcontrol = -1
                            line = line[1:]
                        elif line.startswith("+"):
                            fbcontrol = 1
                            line = line[1:]
                        else:
                            fbcontrol = 0
                        # be relaxed about line parsing
                        # only valid entry.desktop[:action] lines are of interest
                        try:
                            arg = MainArg(line)
                        except:
                            continue
                        if not arg.entry_id or arg.path:
                            continue
                        if (
                            fbcontrol == 0
                            and (arg.entry_id, arg.entry_action) not in terminal_entries
                        ):
                            print_debug(f"got terminal entry {line}")
                            terminal_entries.append((arg.entry_id, arg.entry_action))
                        elif (
                            fbcontrol == -1
                            and not arg.entry_action
                            and arg.entry_id
                            not in excluded_terminal_entries
                            + unexcluded_terminal_entries
                        ):
                            print_debug(f"got fallback exclusion for {line}")
                            excluded_terminal_entries.append(arg.entry_id)
                        elif (
                            fbcontrol == 1
                            and not arg.entry_action
                            and arg.entry_id
                            not in excluded_terminal_entries
                            + unexcluded_terminal_entries
                        ):
                            print_debug(f"got fallback exclusion protection for {line}")
                            unexcluded_terminal_entries.append(arg.entry_id)
                        else:
                            print_debug(
                                f"ignored line { {-1: '-', 0: '', 1: '+'}[fbcontrol] }{line}"
                            )
            except FileNotFoundError:
                pass
            except Exception as caught_exception:
                print_warning(caught_exception, notify=1)

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
        "applications",
        parser=entry_parser_terminal,
        reject_pmt=Terminal.neg_cache,
        reject_ids=excluded_terminal_entries,
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
        os.makedirs(BaseDirectory.xdg_cache_home, exist_ok=True)
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
    Exec given command or Desktop Entry via systemd-run in specific slice.
    If return_cmdline: return cmdline as list
    If fork: return subprocess object.
    """

    # detect desktop entry, update cmdline, app_name
    # cmdline can be empty if terminal is requested with -T
    main_arg = MainArg(cmdline[0] if cmdline else None)

    print_debug("main_arg", main_arg)

    if main_arg.path is not None:
        main_arg.check_path()

    if main_arg.entry_id is not None:
        print_debug("main_arg:", main_arg)

        # if given as a path, try parsing and checking entry directly
        if main_arg.path is not None:
            try:
                entry = DesktopEntry(main_arg.path)
            except Exception as caught_exception:
                raise RuntimeError(
                    f'Failed to parse entry "{main_arg.entry_id}" from "{main_arg.path}"!'
                ) from caught_exception
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
                                print_error(proc_exit_msg, notify=1)
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
        if Terminal.entry.hasKey("TerminalArgExec"):
            terminal_execarg = Terminal.entry.get("TerminalArgExec")
        elif Terminal.entry.hasKey("X-TerminalArgExec"):
            terminal_execarg = Terminal.entry.get("X-TerminalArgExec")
        elif Terminal.entry.hasKey("ExecArg"):
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
        raise ValueError(f"Invalid slice name: {slice_name}!")

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
        *(
            ["--scope"]
            if app_unit_type == "scope"
            else ["--property=Type=exec", "--property=ExitType=cgroup"]
        ),
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
        BaseDirectory.get_runtime_dir(strict=True), "uwsm", "app_daemon_error"
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
        _ = create_fifo("uwsm-app-daemon-out")

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
        os.makedirs(os.path.dirname(error_flag_path), exist_ok=True)
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


def fill_comp_globals():
    """
    Fills vars in CompGlobals:
      cmdline
      cli_args
      id
      id_unit_string
      bin_name
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

    # Deal with ID and main argument
    if Args.parsed.mode == "start":
        # The first argument contains ID and is the main compositor argument
        CompGlobals.id = os.path.basename(Args.parsed.wm_cmdline[0])
        main_arg = MainArg(Args.parsed.wm_cmdline[0])
        if main_arg.path is not None:
            # force hardcode mode
            print_debug("hardcode mode due to main argument being a path")
            Args.parsed.hardcode = True
    elif Args.parsed.mode == "aux":
        # ID is explicit
        CompGlobals.id = Args.parsed.wm_id
        # Assume (for now) this is also a main_arg
        main_arg = MainArg(Args.parsed.wm_id)
        # Should not be a path
        if main_arg.path is not None:
            raise ValueError(f"Aux Compositor ID argument can not be a path")
        # If raw command line is given with non-empty first arg, parse it, replacing main_arg
        if Args.parsed.wm_cmdline and Args.parsed.wm_cmdline[0]:
            main_arg = MainArg(Args.parsed.wm_cmdline[0])
            CompGlobals.cmdline = Args.parsed.wm_cmdline

    if not Val.wm_id.search(CompGlobals.id):
        raise ValueError(
            f'"{CompGlobals.id}" does not conform to "{Val.wm_id.pattern}" pattern!'
        )

    # if in aux exec and have cmdline already, this is all we need
    if (
        Args.parsed.mode == "aux"
        and Args.parsed.aux_action == "exec"
        and CompGlobals.cmdline
    ):
        return

    # escape CompGlobals.id for systemd
    CompGlobals.id_unit_string = simple_systemd_escape(CompGlobals.id, start=False)

    if main_arg.path is not None:
        main_arg.check_path()

    # parse entry
    if main_arg.entry_id is not None:
        print_debug(f"Main arg is a Desktop Entry: {main_arg.entry_id}")

        if main_arg.path is not None:

            # directly parse and check entry
            try:
                entry = DesktopEntry(main_arg.path)
            except Exception as caught_exception:
                raise RuntimeError(
                    f'Failed to parse entry "{main_arg.entry_id}" from "{main_arg.path}"!'
                ) from caught_exception
            check_entry_basic(entry, main_arg.entry_action)

        else:

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
                raise FileNotFoundError(f'Could not find entry "{main_arg.entry_id}"')

            entry = entries[0]

        print_debug("entry", entry)

        entry_dict = entry_action_keys(entry, entry_action=main_arg.entry_action)

        # get Exec from entry as CompGlobals.cmdline if not already filled
        if not CompGlobals.cmdline:
            CompGlobals.cmdline = shlex.split(entry_dict["Exec"])
        CompGlobals.bin_name = os.path.basename(CompGlobals.cmdline[0])

        print_debug(f"self_name: {BIN_NAME}", f"bin_name: {CompGlobals.bin_name}")

        # if desktop entry uses us, deal with the other self.
        entry_uwsm_args = None
        if CompGlobals.bin_name == BIN_NAME:
            try:
                if (
                    "start" not in CompGlobals.cmdline
                    or CompGlobals.cmdline[1] != "start"
                ):
                    raise ValueError(
                        f'Entry "{CompGlobals.id}" uses {BIN_NAME}, but the second argument "{CompGlobals.cmdline[1]}" is not "start"!'
                    )
                # cut ourselves from cmdline to reparse the rest
                CompGlobals.cmdline = CompGlobals.cmdline[1:]

                print_normal(
                    f'Entry "{CompGlobals.id}" uses {BIN_NAME}, reparsing args...'
                )
                # reparse args from entry into separate namespace
                entry_uwsm_args = Args(CompGlobals.cmdline)
                print_debug("entry_uwsm_args.parsed", entry_uwsm_args.parsed)

                # check for various incompatibilities
                entry_main_arg = MainArg(entry_uwsm_args.parsed.wm_cmdline[0])
                if entry_main_arg.entry_id is not None and (
                    main_arg.entry_id,
                    main_arg.entry_action,
                ) == (entry_main_arg.entry_id, entry_main_arg.entry_action):
                    raise ValueError(
                        f'Entry "{CompGlobals.id}" uses {BIN_NAME} that points to itself!'
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

                # parse secondary entry
                if entry_main_arg.entry_id is not None:

                    if entry_main_arg.path is not None:

                        # directly parse and check entry
                        try:
                            entry = DesktopEntry(entry_main_arg.path)
                        except Exception as caught_exception:
                            raise RuntimeError(
                                f'Failed to parse entry "{entry_main_arg.entry_id}" from "{entry_main_arg.path}"!'
                            ) from caught_exception
                        check_entry_basic(entry, entry_main_arg.entry_action)

                    else:

                        # find and parse entry
                        entries = find_entries(
                            "wayland-sessions",
                            parser=entry_parser_by_ids,
                            parser_args={
                                "match_entry_id": entry_main_arg.entry_id,
                                "match_entry_action": entry_main_arg.entry_action,
                            },
                        )
                        if not entries:
                            raise FileNotFoundError(
                                f'Could not find entry "{entry_main_arg.entry_id}"'
                            )

                        entry = entries[0]

                    print_debug("entry", entry)

                    entry_dict = entry_action_keys(
                        entry, entry_action=entry_main_arg.entry_action
                    )

                    # get Exec from entry as CompGlobals.cmdline
                    entry_cmdline = shlex.split(entry_dict["Exec"])

                    if os.path.basename(entry_cmdline[0]) == BIN_NAME:
                        raise ValueError(
                            f'Entry "{CompGlobals.id}" uses {BIN_NAME} that points to another Entry that also uses {BIN_NAME}!'
                        )

                    # combine Exec from secondary entry with arguments from primary entry
                    # TODO: either drop this behavior, or add support for % fields
                    # not that wayland session entries will ever use them
                    entry_uwsm_args.parsed.wm_cmdline = (
                        entry_cmdline + entry_uwsm_args.parsed.wm_cmdline[1:]
                    )

                # replace CompGlobals.cmdline with Args.parsed.wm_cmdline from entry
                CompGlobals.cmdline = entry_uwsm_args.parsed.wm_cmdline
                CompGlobals.bin_name = os.path.basename(CompGlobals.cmdline[0])

            except Exception as caught_exception:
                print_error(caught_exception)
                sys.exit(1)

        # combine Exec from entry and arguments
        # TODO: either drop this behavior, or add support for % fields
        # not that wayland session entries will ever use them
        CompGlobals.cmdline = CompGlobals.cmdline + Args.parsed.wm_cmdline[1:]

        print_debug("CompGlobals.cmdline", CompGlobals.cmdline)

        # this excludes aux exec mode
        if Args.parsed.mode == "start" or (
            Args.parsed.mode == "aux" and Args.parsed.aux_action == "prepare-env"
        ):
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
                    raise ValueError(
                        f'{BIN_NAME} in entry "{CompGlobals.id}" requests exclusive desktop names ("-e") but has no desktop names listed via "-D"!'
                    )
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
                    + [CompGlobals.bin_name]
                    + (
                        sane_split(entry_uwsm_args.parsed.desktop_names, ":")
                        if entry_uwsm_args is not None
                        else []
                    )
                    + sane_split(Args.parsed.desktop_names, ":")
                )
            print_debug("CompGlobals.desktop_names", CompGlobals.desktop_names)

            # fill name and description with fallbacks from: CLI, nested CLI, Entry (without action)
            if Args.parsed.wm_name:
                CompGlobals.name = Args.parsed.wm_name
            elif entry_uwsm_args is not None and entry_uwsm_args.parsed.wm_name:
                CompGlobals.name = entry_uwsm_args.parsed.wm_name
            else:
                CompGlobals.name = " - ".join(
                    n for n in (entry.getName(), entry.getGenericName()) if n
                )

            if Args.parsed.wm_comment:
                CompGlobals.description = Args.parsed.wm_comment
            elif entry_uwsm_args is not None and entry_uwsm_args.parsed.wm_comment:
                CompGlobals.description = entry_uwsm_args.parsed.wm_comment
            elif entry.getComment():
                CompGlobals.description = entry.getComment()

            # inherit slice argument
            if entry_uwsm_args is not None and Args.parsed.use_session_slice is None:
                print_debug(
                    "inherited use_session_slice",
                    entry_uwsm_args.parsed.use_session_slice,
                )
                Args.parsed.use_session_slice = entry_uwsm_args.parsed.use_session_slice

            # inherit hardcode argument
            if (
                Args.parsed.mode == "start"
                and entry_uwsm_args is not None
                and entry_uwsm_args.parsed.hardcode
            ):
                print_debug("inherited hardcode", entry_uwsm_args.parsed.hardcode)
                Args.parsed.hardcode = True

        # reparse and check resulting main arg
        main_arg = MainArg(CompGlobals.cmdline[0])
        if main_arg.path is None:
            main_arg.check_exec()
        else:
            main_arg.check_path()

    elif main_arg.executable is not None:
        print_debug(f"Main arg is an executable: {main_arg.executable}")

        if main_arg.path is None:
            main_arg.check_exec()
        else:
            main_arg.check_path()

        # fill cmdline from parsed cmdline if not already filled
        if not CompGlobals.cmdline:
            CompGlobals.cmdline = Args.parsed.wm_cmdline
        # in aux cmdline or its first item might be empty, which means ID is the executable
        if not CompGlobals.cmdline:
            CompGlobals.cmdline = [main_arg.executable]
        elif not CompGlobals.cmdline[0]:
            CompGlobals.cmdline[0] = main_arg.executable
        CompGlobals.bin_name = os.path.basename(CompGlobals.cmdline[0])

        # this excludes aux exec mode
        if Args.parsed.mode == "start" or (
            Args.parsed.mode == "aux" and Args.parsed.aux_action == "prepare-env"
        ):
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
                    + [CompGlobals.bin_name]
                    + sane_split(Args.parsed.desktop_names, ":")
                )
            CompGlobals.name = Args.parsed.wm_name
            CompGlobals.description = Args.parsed.wm_comment
            print_debug("CompGlobals.desktop_names", CompGlobals.desktop_names)

    else:
        raise ValueError("Could not determine or parse main argument")

    # main_arg should not be an entry after all this parsing
    if main_arg.entry_id is not None:
        raise RuntimeError(f"Could not parse {main_arg} down to executable!")

    # Canonicalize to absolute path if in hardcode mode
    # or in case path for some reason is relative
    # Path in main_arg object is already normalized
    if Args.parsed.mode == "start" and (
        Args.parsed.hardcode or main_arg.path is not None
    ):
        canon_arg = os.path.abspath(main_arg.path or which(main_arg.executable))
        print_debug("normalized", CompGlobals.cmdline[0], canon_arg)
        CompGlobals.cmdline[0] = os.path.abspath(canon_arg)

    # fill cli-exclusive compositor arguments for reproduction in unit drop-ins
    CompGlobals.cli_args = Args.parsed.wm_cmdline[1:]

    # this excludes aux exec mode
    if Args.parsed.mode == "start" or (
        Args.parsed.mode == "aux" and Args.parsed.aux_action == "prepare-env"
    ):
        CompGlobals.cli_desktop_names = sane_split(Args.parsed.desktop_names, ":")
        CompGlobals.cli_desktop_names_exclusive = Args.parsed.desktop_names_exclusive
        CompGlobals.cli_name = Args.parsed.wm_name
        CompGlobals.cli_description = Args.parsed.wm_comment

        # deduplicate desktop names, preserving order
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
        "(^[^a-zA-Z]|[^a-zA-Z0-9_])+", "_", CompGlobals.bin_name
    ).lower()

    return


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


def waitpid(pid: int):
    "Waits for given PID to exit"
    try:
        pid_fd = os.pidfd_open(pid)
    except ProcessLookupError:
        print_normal(f"Process with PID {pid} not found.")
        return
    select([pid_fd], [], [])
    print_normal(f"Process with PID {pid} has ended.")
    return


def waitenv(varnames: List[str] = None, timeout=10, step=0.5):
    "Waits for varnames to appear in activation environment"
    if varnames is None:
        varnames = ["WAYLAND_DISPLAY"]
    else:
        varnames = filter_varnames(varnames)
    varnames_set = set(varnames)
    varnames_exist_set = set()
    bus_session = DbusInteractions("session")
    start_ts = time.time()
    for attempt in range(1, int(timeout // step) + 1):
        aenv_varnames_set = set(bus_session.get_systemd_vars().keys())
        if varnames_set.issubset(aenv_varnames_set):
            print_ok(
                f"All expected variables appeared in activation environment:\n  {', '.join(varnames)}"
            )
            return
        varnames_appeared_set = varnames_set.intersection(aenv_varnames_set).difference(
            varnames_exist_set
        )
        if varnames_appeared_set:
            varnames_exist_set.update(varnames_appeared_set)
            print_normal(
                f"Expected variables appeared in activation environment:\n  {', '.join(varnames_appeared_set)}\nStill expecting:\n  {', '.join(varnames_set.difference(varnames_exist_set))}"
            )
        if time.time() - start_ts > timeout:
            break
        time.sleep(step)
    raise TimeoutError(
        f"Timed out waiting for variables in activation environment:\n  {', '.join(varnames_set.difference(varnames_exist_set))}"
    )


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
                # TRANSITION: move config to subdir
                old_config = os.path.join(
                    BaseDirectory.xdg_config_home, f"{BIN_NAME}-default-id"
                )
                if select_wm_id == default_id and not os.path.isfile(old_config):
                    print_normal(f"Default compositor ID unchanged: {select_wm_id}.")
                else:
                    save_default_comp_entry(select_wm_id)
                sys.exit(0)
            else:
                print_warning("No compositor was selected.")
                sys.exit(1)
        except Exception as caught_exception:
            print_error(caught_exception)
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
                    # TRANSITION: move config to subdir
                    old_config = os.path.join(
                        BaseDirectory.xdg_config_home, f"{BIN_NAME}-default-id"
                    )
                    if select_wm_id == default_id and not os.path.isfile(old_config):
                        print_normal(
                            f"Default compositor ID unchanged: {select_wm_id}."
                        )
                    else:
                        save_default_comp_entry(select_wm_id)
                    # update Args.parsed.wm_cmdline in place
                    Args.parsed.wm_cmdline = [select_wm_id]
                else:
                    print_error("No compositor was selected!")
                    sys.exit(1)
            except Exception as caught_exception:
                print_error(caught_exception)
                sys.exit(1)

        try:
            fill_comp_globals()

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
                print_error(
                    "A compositor or graphical-session* target is already active!"
                )
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

            # start bindpid service on our PID
            sprc = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "start",
                    f"wayland-session-bindpid@{os.getpid()}.service",
                ],
                check=True,
            )
            print_debug(sprc)

            # fork out a process that will hold session scope open
            # until compositor unit is stopped
            mainpid = os.getpid()
            childpid = os.fork()
            if childpid == 0:
                # ignore HUP and TERM
                signal.signal(signal.SIGHUP, signal.SIG_IGN)
                signal.signal(signal.SIGTERM, signal.SIG_IGN)

                # 15 seconds should be more than enough to wait for compositor activation
                # 10 seconds unit timeout plus 5 on possible overhead
                # Premature exit is covered explicitly
                # 0.5s between 30 attempts
                bus_session = DbusInteractions("session")
                print_debug("bus_session holder fork", bus_session)
                for attempt in range(30, -1, -1):
                    # if parent process exits at this stage, silently exit
                    try:
                        os.kill(mainpid, 0)
                    except ProcessLookupError:
                        print_debug(
                            "holder exiting due to premature parent process end"
                        )
                        sys.exit(0)

                    # timed out
                    if attempt == 0:
                        print_warning(
                            f"Timed out waiting for activation of wayland-wm@{CompGlobals.id_unit_string}.service"
                        )
                        try:
                            print_debug("killing main process from holder")
                            os.kill(mainpid, signal.SIGTERM)
                        except ProcessLookupError:
                            print_debug("main process already absent")
                            pass
                        sys.exit(1)

                    time.sleep(0.5)
                    print_debug(f"holder attempt {attempt}")

                    # query systemd dbus for active matching units
                    units = bus_session.list_units_by_patterns(
                        ["active", "activating"],
                        [f"wayland-wm@{CompGlobals.id_unit_string}.service"],
                    )
                    print_debug("holder units", units)
                    if len(units) > 0:
                        break

                # Strangely, MainPID unit property is not accessible via DBus.
                # systemctl to the rescue!
                sprc = subprocess.run(
                    [
                        "systemctl",
                        "--user",
                        "show",
                        "--property",
                        "MainPID",
                        "--value",
                        f"wayland-wm@{CompGlobals.id_unit_string}.service",
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                )
                cpid = sprc.stdout.strip()
                if not cpid or not cpid.isnumeric():
                    print_warning(
                        f"Could not get MainPID of wayland-wm@{CompGlobals.id_unit_string}.service"
                    )
                    sys.exit(1)

                print_normal(f"Holding until PID {cpid} exits")
                # use lightweight waitpid if available
                if which("waitpid"):
                    os.execlp("waitpid", "waitpid", "-e", cpid)
                else:
                    waitpid(int(cpid))
                    sys.exit(0)
            # end of fork

            # replace ourselves with systemctl
            # this will start the main compositor unit
            # and wait until compositor is stopped
            os.execlp(
                "systemctl",
                "systemctl",
                "--user",
                "start",
                "--wait",
                f"wayland-wm@{CompGlobals.id_unit_string}.service",
            )

        except Exception as caught_exception:
            print_error(caught_exception)
            sys.exit(1)

    #### STOP
    elif Args.parsed.mode == "stop":
        try:
            stop_wm()
            stop_rc = 0
        except Exception as caught_exception:
            print_error(caught_exception)
            stop_rc = 1

        # Args.parsed.remove_units is False when not given, None if given without argument
        if Args.parsed.remove_units is not False:
            remove_units(Args.parsed.remove_units)
            if UnitsState.changed:
                try:
                    reload_systemd()
                except Exception as caught_exception:
                    print_warning(caught_exception)
            else:
                print_normal("Units unchanged.")

        sys.exit(stop_rc)

    #### FINALIZE
    elif Args.parsed.mode == "finalize":
        try:
            finalize(
                Args.parsed.env_names + os.getenv("UWSM_FINALIZE_VARNAMES", "").split()
            )
        except Exception as caught_exception:
            print_error(caught_exception, notify=1)

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
            print_error(caught_exception, notify=1)
            sys.exit(1)

    #### CHECK
    elif Args.parsed.mode == "check" and Args.parsed.checker == "is-active":
        try:
            if is_active(Args.parsed.wm, Args.parsed.verbose):
                sys.exit(0)
            else:
                sys.exit(1)
        except Exception as caught_exception:
            print_error(caught_exception)
            sys.exit(1)

    elif Args.parsed.mode == "check" and Args.parsed.checker == "may-start":
        already_active_msg = (
            "A compositor and/or graphical-session* targets are already active"
        )
        dealbreakers = []

        try:
            if is_active():
                dealbreakers.append(already_active_msg)
        except Exception as caught_exception:
            print_error("Could not check for active compositor!")
            print_error(caught_exception)
            sys.exit(1)

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
            print_error(caught_exception)
            sys.exit(1)
        if not parent_cmdline.startswith("-"):
            dealbreakers.append("Not in login shell")

        # check foreground VT
        fgvt = get_fg_vt()
        if fgvt is None:
            print_error("Could not determine foreground VT")
            sys.exit(1)
        else:
            # argparse does not pass default for this
            allowed_vtnr = Args.parsed.vtnr or [1]
            if fgvt not in allowed_vtnr:
                dealbreakers.append(
                    f"Foreground VT ({fgvt}) is not among allowed VTs ({'|'.join([str(v) for v in allowed_vtnr])})"
                )

        # check for graphical target
        if Args.parsed.gst_seconds > 0:
            try:
                bus_system = DbusInteractions("system")
                print_debug("bus_system initial", bus_system)

                # initial check
                units = bus_system.list_units_by_patterns(
                    ["active", "activating"], ["graphical.target"]
                )
                print_debug("graphical.target units", units)

                if len(units) < 1:
                    # check if graphical.target is queued for startup,
                    # wait if it is, recheck when it leaves queue.
                    gst_seen_in_queue = False
                    spacer = ""
                    for attempt in range(Args.parsed.gst_seconds, -1, -1):
                        jobs = (
                            (str(unit), str(state))
                            for _, unit, state, _, _, _ in bus_system.list_systemd_jobs()
                        )
                        print_debug("system jobs", jobs)
                        if ("graphical.target", "start") in jobs:
                            gst_seen_in_queue = True
                            if (
                                not Args.parsed.quiet
                                and attempt == Args.parsed.gst_seconds
                            ):
                                print_normal(
                                    f"graphical.target is queued for start, waiting for {Args.parsed.gst_seconds}s...",
                                )
                            elif not Args.parsed.quiet and (
                                attempt % 10 == 0 or attempt == 15 or attempt < 10
                            ):
                                print_normal(f"{spacer}{attempt}", end="")
                                spacer = " "
                            if attempt != 0:
                                time.sleep(1)
                        else:
                            break

                    if not Args.parsed.quiet and gst_seen_in_queue:
                        print_normal("")

                    # recheck graphical.target
                    units = bus_system.list_units_by_patterns(
                        ["active", "activating"], ["graphical.target"]
                    )
                    print_debug("graphical.target units", units)
                    if len(units) < 1:
                        if not Args.parsed.quiet and gst_seen_in_queue:
                            print_warning("Timed out.")
                        dealbreakers.append("System has not reached graphical.target")
            except Exception as caught_exception:
                print_error("Could not check if graphical.target is reached!")
                print_error(caught_exception)
                sys.exit(1)

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
        manager_pid = int(os.getenv("MANAGERPID", "0"))
        ppid = int(os.getppid())
        print_debug(f"manager_pid: {manager_pid}, ppid: {ppid}")
        if not manager_pid or manager_pid != ppid:
            print_error("Aux actions can only be run by systemd user manager!")
            sys.exit(1)

        if Args.parsed.aux_action == "prepare-env":
            fill_comp_globals()
            try:
                prepare_env()
                sys.exit(0)
            except Exception as caught_exception:
                print_error(caught_exception)
                try:
                    cleanup_env()
                except Exception as caught_exception:
                    print_error(caught_exception)
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
                    print_error(caught_exception)
                    sys.exit(1)
        elif Args.parsed.aux_action == "exec":
            try:
                fill_comp_globals()
                print_debug(CompGlobals.cmdline)
                print_normal(f"Starting: {shlex.join(CompGlobals.cmdline)}...")

                # get current systemd environment
                bus_session = DbusInteractions("session")
                env_pre = filter_varnames(bus_session.get_systemd_vars())

                # fork out a process that will watch for expected variables
                # in activation environment and signal unit readiness automatically
                mainpid = os.getpid()
                childpid = os.fork()
                if childpid == 0:
                    try:
                        waitenv(
                            varnames=["WAYLAND_DISPLAY"]
                            + os.getenv("UWSM_WAIT_VARNAMES", "").split()
                        )
                        # just to be on the safe side if things are settling down
                        settle_time = os.getenv("UWSM_WAIT_VARNAMES_SETTLETIME", "0.2")
                        try:
                            settle_time = float(settle_time)
                        except:
                            print_warning(f"\"UWSM_WAIT_VARNAMES_SETTLETIME\" contains invalid value \"{settle_time}\", using \"0.2\"")
                            settle_time = 0.2
                        time.sleep(settle_time)

                        # calculate environment delta and update cleanup list
                        env_post = filter_varnames(bus_session.get_systemd_vars())
                        env_delta = dict(set(env_post.items()) - set(env_pre.items()))

                        if env_delta:
                            try:
                                append_to_cleanup_file(
                                    CompGlobals.id, env_delta, skip_always_cleanup=True, create=True
                                )
                            except FileNotFoundError as caught_exception:
                                print_error(
                                    caught_exception, "Assuming env preloader failed"
                                )
                                os.kill(mainpid, signal.SIGTERM)
                                sys.exit(1)

                        if get_active_wm_unit(active=False, activating=True):
                            print_normal(f"Declairng unit for {CompGlobals.id} ready.")
                            os.execlp("systemd-notify", "systemd-notify", "--ready")
                        else:
                            print_normal(
                                f"Unit for {CompGlobals.id} is already active."
                            )
                            sys.exit(0)
                    except Exception as caught_exception:
                        print_warning("Autoready failed:\n", caught_exception)
                        sys.exit(1)
                    # end of fork
                # execute compositor cmdline
                os.execlp(CompGlobals.cmdline[0], *(CompGlobals.cmdline))
            except Exception as caught_exception:
                print_error(caught_exception)
                sys.exit(1)

        elif Args.parsed.aux_action == "app-daemon":
            print_normal("Launching app daemon", file=sys.stderr)
            try:
                app_daemon()
            except Exception as caught_exception:
                print_error(caught_exception)
                sys.exit(1)

        elif Args.parsed.aux_action == "waitpid":
            try:
                waitpid(Args.parsed.pid)
                sys.exit(0)
            except Exception as caught_exception:
                print_error(caught_exception)
                sys.exit(1)

        elif Args.parsed.aux_action == "waitenv":
            try:
                waitenv(
                    varnames=["WAYLAND_DISPLAY"]
                    + Args.parsed.env_names
                    + os.getenv("UWSM_WAIT_VARNAMES", "").split()
                )
                # just to be on the safe side if things are settling down
                settle_time = os.getenv("UWSM_WAIT_VARNAMES_SETTLETIME", "0.2")
                try:
                    settle_time = float(settle_time)
                except:
                    print_warning(f"\"UWSM_WAIT_VARNAMES_SETTLETIME\" contains invalid value \"{settle_time}\", using \"0.2\"")
                    settle_time = 0.2
                time.sleep(settle_time)
                sys.exit(0)
            except Exception as caught_exception:
                print_error(caught_exception)
                sys.exit(1)
