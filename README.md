# Universal Wayland Session Manager

Experimental tool that wraps any standalone Wayland compositor into a set of systemd units to
provide graphical user session with environment management, XDG autostart support, clean shutdown.

WIP(ish). Use at your onw risk.

The main structure of subcommands and features is more or less settled and will likely
not receive any drastic changes unless some illuminative idea comes by.
Nonetheless, keep an eye for commits with `[Breaking]` messages.

## Concepts and features

- Uses systemd units and dependencies for startup, operation, and shutdown:
  - Binds to the basic [structure](https://systemd.io/DESKTOP_ENVIRONMENTS/#pre-defined-systemd-units)
    of `graphical-session-pre.target`, `graphical-session.target`, `xdg-desktop-autostart.target`.
  - Aadds custom nested slices `app-graphical.slice`, `background-graphical.slice`, `session-graphical.slice`
    to put apps in and terminate them cleanly on exit.
  - Provides convenient way of [launching apps to those slices](https://systemd.io/DESKTOP_ENVIRONMENTS/#xdg-standardization-for-applications).
- Systemd units are treated with hierarchy and universality in mind:
  - Templated units with specifiers.
  - Named from common to specific where possible.
  - Allowing for high-level `name-.d` drop-ins.
- Compositor-specific behavior can be added by plugins. Currently supported: sway, wayfire, labwc
- Idempotently (well, best-effort-idempotently) handles environment:
  - On startup environment is prepared by:
    - sourcing shell profile
    - sourcing `wayland-session-env`, `wayland-session-env-${desktop}` files
      from each dir of reversed `${XDG_CONFIG_HOME}:${XDG_CONFIG_DIRS}` (in increasing priority),
      where `${desktop}` is each item of `${XDG_CURRENT_DESKTOP}`, lowercased
  - Difference between environment state before and after preparation is exported into systemd user manager
    and dbus activation environment
  - On shutdown previously exported variables are unset from systemd user manager
    (dbus activation environment does not support unsetting, so those vars are emptied instead (!))
  - Lists of variables for export and cleanup are determined algorithmically by:
    - comparing environment before and after preparation procedures
    - boolean operations with predefined lists
- Can work with Desktop entries from `wayland-sessions` in XDG data hierarchy in two different scenarios:
  - Actively select and launch compositor from Desktop entry (which is used as compositor instance ID):
    - Data taken from entry (Can be amended or overridden via cli arguments):
      - `Exec` for argument list
      - `DesktopNames` for `XDG_CURRENT_DESKTOP` and `XDG_SESSION_DESKTOP`
      - `Name` and `Comment` for unit `Description`
    - Entries can be overridden, masked or added in `${XDG_DATA_HOME}/wayland-sessions/`
    - Optional interactive selector (requires whiptail), choice is saved in `${XDG_CONFIG_HOME}/wayland-session-default-id`
    - Desktop entry [actions](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s11.html) are supported
  - Be launched via a Desktop entry by a login/display manager.
- Can run with arbitrary compositor command line (saved as a unit drop-in).
- Provides better control of XDG autostart apps.
  - XDG autostart services (`app-*@autostart.service` units) are placed into `app-graphical.slice`
    that receives stop action before compositor is stopped.
  - Can be mass-controlled via stopping and starting `wayland-session-xdg-autostart@${compositor}.target`
- Tries best to shutdown session cleanly via a net of dependencies between units
- Provides helpers for various operations:
  - finalizing service startup (compositor service unit uses `Type=notify`) and exporting variables set by compositor
  - launching applications as scopes or services in proper slices
  - checking conditions for launch at login (for integration into login shell profile)

## Installation

### 1. Executables and plugins

Put `wayland-session` executable somewhere in `$PATH`.

Put `wayland-session-plugins` dir somewhere in `${HOME}/.local/lib:/usr/local/lib:/usr/lib:/lib` (`UWSM_PLUGIN_PREFIX_PATH`)

### 2. Vars set by compositor and Startup notification

Ensure your compositor runs `wayland-session finalize` at startup:

- it fills systemd and dbus environments with essential vars set by compositor: `WAYLAND_DISPLAY`, `DISPLAY`
- any other vars can be given as arguments by name
- any exported variables are also added to cleanup list
- if environment export is successful, it signals compositor service readiness,
  so `graphical-session.target` can properly be declared reached.

Example snippet for sway config:

`exec exec wayland-session finalize SWAYSOCK I3SOCK XCURSOR_SIZE XCURSOR_THEME`

### 3. Slices

By default `wayland-session` launhces compositor service in `app.slice` and all processes spawned by compositor
will be a part of `wayland-wm@${compositor}.service` unit. This works, but is not an optimal solution.

Systemd [documentation](https://systemd.io/DESKTOP_ENVIRONMENTS/#pre-defined-systemd-units)
recommends running compositors in `session.slice` and launch apps as scopes or services in `app.slice`.

`wayland-session` provides convenient way of handling this, It generates special nested slices that will
also receive stop action ordered before `wayland-wm@${compositor}.service` shutdown:

- `app-graphical.slice`
- `background-graphical.slice`
- `session-graphical.slice`

`app-*@autostart.service` units are also modified to be started in `app-graphical.slice`.

To launch an app inside one of those slices, use:

`wayland-session app [-s a|b|s|custom.slice] [-t scope|service] -- your_app [with args]`

Launching desktop entries is also supported:

`wayland-session app [-s a|b|s|custom.slice] [-t scope|service] -- your_app.desktop[:action] [with args]`

In this case args must be supported by the entry or its selected action according to
[XDG Desktop Entry Specification](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s07.html).

Always use `--` to disambiguate command line if any dashed arguments are intended for launched app.

Example snippets for sway config for launching apps:

Launch [proposed](https://gitlab.freedesktop.org/terminal-wg/specifications/-/merge_requests/3) default terminal:

`bindsym --to-code $mod+t exec exec wayland-session app -T`

Fuzzel has a very handy launch-prefix option:

`bindsym --to-code $mod+r exec exec fuzzel --launch-prefix='wayland-session app --' --log-no-syslog`

Launch SpaceFM via desktop entry:

`bindsym --to-code $mod+e exec exec wayland-session app spacefm.desktop`

Featherpad desktop entry has "standalone-window" action:

`bindsym --to-code $mod+n exec exec wayland-session app featherpad.desktop:standalone-window`

When app launching is properly configured, compositor service itself can be placed in `session.slice` by setting
environment variable `UWSM_USE_SESSION_SLICE=true` before generating units (best to export this
in `profile` before `wayland-session` invocation). Or by adding `-S` argument to `start` subcommand.

Unit type of launched apps can be controlled by `-t service|scope` argument or setting its default
via `UWSM_APP_UNIT_TYPE` env var.

## Operation

### Syntax

`-h|--help` option is available for `wayland-session` and its subcommands.

Start variants:

- `wayland-session start ${compositor}`: generates and starts templated units with `@${compositor}` instance.
- `wayland-session start -- ${compositor} with "any complex" --arguments`: also adds arguments for particular `@${compositor}` instance.
- Optional parameters to provide more metadata:
  - `-[a|e]D DesktopName1[:DesktopMame2:...]`: append (`-a`) or exclusively set (`-e`) `${XDG_CURRENT_DESKTOP}`
  - `-N Name`
  - `-C "Compositor description"`

Always use `--` to disambiguate command line if any dashed arguments are intended for launched compositor.

`${compositor}` can be an executable or a valid [desktop entry ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s02.html#desktop-file-id)
(optionally with an [action ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s11.html) appended via ':')
In the latter case `wayland-session` will get desktop entry  from `wayland-sessions` data hierarchy, and use `Exec` and `DesktopNames` from it
(along with `Name` and `Comment` for unit descriptons).

Arguments provided on command line are appended to the command line of desktop entry (unlike applications),
no argument processing is done (Please [file a bug report](https://github.com/Vladimir-csp/uwsm/issues/new/choose)
if you encounter any wayland-sessions desktop entry with `%`-fields).

If you want to customize compositor execution provided with a desktop entry, copy it to
`~/.local/share/wayland-sessions/` and change to your liking, including adding [actions](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s11.html).

If `${compositor}` is `select` or `default`, `wayland-session` invokes a menu to select desktop entries available in
`wayland-sessions` data hierarchy (including their actions). Selection is saved, previous selection is highlighted
(or launched right away in case of `default`). Selected entry is used as instance ID.

There is also a separate `select` action (`wayland-session select`) that only selects and saves default `${compositor}`
and does nothing else, which is handy for seamless shell profile integration.

Compositor command line (positonal arguments starting with `${compositor}`) can be separated from optional arguments by `--` to
avoid ambiguous parsing.

When started, `wayland-session` will wait while wayland session is running, and terminate session if
is itself interrupted or terminated.

### Where to launch from

#### Shell profile integration

To launch automatically after login on virtual console 1, if systemd is at `graphical.target`,
add this to shell profile:

    if wayland-session check may-start && wayland-session select
    then
    	exec wayland-session start default
    fi

`check may-start` checker subcommand, among other things, **screens for being in interactive login shell,
which is essential**, since profile sourcing can otherwise lead to nasty loops.

Stop with `wayland-session stop` or `systemctl --user stop wayland-session@*.service`.

#### From display manager

To launch uwsm from a display/login manager, `wayland-session` can be used inside desktop entries.
Example `/usr/local/share/wayland-sessions/my-compositor.desktop`:

    [Desktop Entry]
    Name=My compositor (with UWSM)
    Comment=My cool compositor
    Exec=wayland-session start -N "My compositor" -D mycompositor -C "My cool compositor" mywm
    DesktopNames=mycompositor
    Type=Application

Things to keep in mind:

- For consistency, command line arguments should mirror the keys of the entry
- Command in `Exec=` should start with `wayland-session`
- It should not launch a desktop entry, only an executable.

Potentially such entries may be found and used by `wayland-session` itself, i.e. in shell profile integration
situation, or when launched manually. Following the principles above ensures `wayland-session` will properly
recognize itself and parse requested arguments inside the entry without any side effects.

## Longer story, tour under the hood:

### Start and bind

(At least for now) units are generated by the script.

Run `wayland-session start -o ${compositor}` to populate `${XDG_RUNTIME_DIR}/systemd/user/` with them and do
nothing else (`-o`).

Any remainder arguments are appended to compositor argument list (even when `${compositor}` is a desktop entry).
Use `--` to disambigue:

`wayland-session start -o -- ${compositor} with "any complex" --arguments`

Desktop entries can be overridden or added in `${XDG_DATA_HOME}/wayland-sessions/`.

Basic set of generated units:

- templated targets boud to stock systemd user-level targets
  - `wayland-session-pre@.target`
  - `wayland-session@.target`
  - `wayland-session-xdg-autostart@.target`
- templated services
  - `wayland-wm-env@.service` - environment preloader service
  - `wayland-wm@.service` - main compositor service
- slices for apps nested in stock systemd user-level slices
  - `app-graphical.slice`
  - `background-graphical.slice`
  - `session-graphical.slice`
- tweaks
  - `wayland-wm-env@${compositor}.service.d/custom.conf`, `wayland-wm@${compositor}.service.d/custom.conf` - if arguments and/or various names were given on command line, they go here.
  - `app-@autostart.service.d/slice-tweak.conf` - assigns XDG autostart apps to `app-graphical.slice`

After units are generated, compositor can be started by: `systemctl --user start wayland-wm@${compositor}.service`

Add `--wait` to hold terminal until session ends.

`exec` it from login shell to bind to login session:

`exec systemctl --user start --wait wayland-wm@${compositor}.service`

Still if login session is terminated, wayland session will continue running, most likely no longer being accessible.

To also bind it the other way around, shell traps are used:

`trap "if systemctl --user is-active -q wayland-wm@${compositor}.service ; then systemctl --user --stop wayland-wm@${compositor}.service ; fi" INT EXIT HUP TERM`

This makes the end of login shell also be the end of wayland session.

When `wayland-wm-env@.service` is started during `graphical-session-pre.target` startup,
`wayland-session aux prepare-env ${compositor}` is launched (with shared set of custom arguments).

It runs shell code to prepare environment, that sources shell profile, `wayland-session-env*` files,
anything that plugins dictate. Environment state at the end of shell code is given back to the main process.
`wayland-session` is also smart enough to find login session associated with current TTY
and set `$XDG_SESSION_ID`, `$XDG_VTNR`.

The difference between initial env (that is the state of activation environment) and after all the
sourcing and setting is done, plus `varnames.always_export`, minus `varnames.never_export`, is added to
activation environment of systemd user manager and dbus.

Those variable names, plus `varnames.always_cleanup` minus `varnames.never_cleanup` are written to
a cleanup list file in runtime dir.

### Startup finalization

`wayland-wm@.service` uses `Type=notify` and waits for compositor to signal started state.
Activation environments will also need to receive essential variables like `WAYLAND_DISPLAY`
to launch graphical applications successfully.

`wayland-session finalize [VAR [VAR2...]]` runs:

    dbus-update-activation-environment --systemd WAYLAND_DISPLAY DISPLAY [VAR [VAR2...]]
    systemctl --user import-environment WAYLAND_DISPLAY DISPLAY [VAR [VAR2...]]
    systemd-notify --ready

The first two together might be an overkill.

Only defined variables are used. Variables that are not blacklisted by `varnames.never_cleanup` set
are also added to cleanup list in runtime dir.

### Stop

Just stop the main service: `systemctl --user stop "wayland-wm@${compositor}.service"`, everything else will
stopped by systemd.

Wildcard `systemctl --user stop "wayland-wm@*.service"` will also work.

If start command was run with `exec` from login shell or `.profile`,
this stop command also doubles as a logout command.

When `wayland-wm-env@${compositor}.service` is stopped, `wayland-session aux cleanup-env` is launched.
It looks for **any** cleanup files (`env_names_for_cleanup_*`) in runtime dir. Listed variables,
plus `varnames.always_cleanup` minus `varnames.never_cleanup`
are emptied in dbus activation environment and unset from systemd user manager environment.

When no compositor is running, units can be removed (`-r`) by `wayland-session stop -r`.

Add compositor to `-r` to remove only customization drop-ins: `wayland-session stop -r ${compositor}`.

### Profile integration

This example does the same thing as `check may-start` + `start` subcommand combination described earlier:
starts wayland session automatically upon login on tty1 if system is in `graphical.target`

**Screening for being in interactive login shell here is essential** (`[ "${0}" != "${0#-}" ]`).
`wayland-wm-env@${compositor}.service` sources profile, which has a potential for nasty loops if run
unconditionally. Other conditions are a recommendation:

    MY_COMPOSITOR=sway
    if [ "${0}" != "${0#-}" ] && \
       [ "$XDG_VTNR" = "1" ] && \
       systemctl is-active -q graphical.target && \
       ! systemctl --user is-active -q wayland-wm@*.service
    then
        wayland-session start -o ${MY_COMPOSITOR}
        trap "if systemctl --user is-active -q wayland-wm@${MY_COMPOSITOR}.service ; then systemctl --user --stop wayland-wm@${MY_COMPOSITOR}.service ; fi" INT EXIT HUP TERM
        echo Starting ${MY_COMPOSITOR} compositor
        systemctl --user start --wait wayland-wm@${MY_COMPOSITOR}.service &
        wait
        exit
    fi

## Compositor-specific actions

Shell plugins provide compositor-specific functions during environment preparation.

Named `${__WM_BIN_ID__}.sh.in`, they should only contain specifically named functions.

`${__WM_BIN_ID__}` is derived from the item 0 of compositor command line by applying `s/(^[^a-zA-Z]|[^a-zA-Z0-9_])+/_/`

It is used as plugin id and suffix in function names.

Variables available to plugins:

- `__WM_ID__` - compositor ID, effective first argument of `start`.
- `__WM_BIN_ID__` - processed first item of compositor argv.
- `__WM_DESKTOP_NAMES__` - `:`-separated desktop names from `DesktopNames=` of entry and `-D` cli argument.
- `__WM_FIRST_DESKTOP_NAME__` - first of the above.
- `__WM_DESKTOP_NAMES_LOWERCASE__` - same as the above, but in lower case.
- `__WM_FIRST_DESKTOP_NAME_LOWERCASE__` - first of the above.
- `__WM_DESKTOP_NAMES_EXCLUSIVE__` - (`true`|`false`) indicates that `__WM_DESKTOP_NAMES__` came from cli argument
  and are marked as exclusive.
- `__OIFS__` - contains shell default field separator (space, tab, newline) for convenient restoring.

Standard functions:

- `load_wm_env` - standard function for loading env files
- `process_config_dirs_reversed` - called by `load_wm_env`,
  iterates over XDG_CONFIG hierarchy in reverse (increasing priority)
- `in_each_config_dir_reversed` - called by `process_config_dirs_reversed` for each config dir,
  loads `wayland-session-env`, `wayland-session-env-${desktop}` files
- `process_config_dirs` - called by `load_wm_env`,
  iterates over XDG_CONFIG hierarchy (decreasing priority)
- `in_each_config_dir` - called by `process_config_dirs` for each config dir, does nothing ATM
- `source_file` - sources `$1` file, providing messages for log.

See code inside `wayland-session` for more auxillary funcions.

Functions that can be added by plugins, replacing standard funcions:

- `quirks__${__WM_BIN_ID__}` - called before env loading.
- `load_wm_env__${__WM_BIN_ID__}`
- `process_config_dirs_reversed__${__WM_BIN_ID__}`
- `in_each_config_dir_reversed__${__WM_BIN_ID__}`
- `process_config_dirs__${__WM_BIN_ID__}`
- `in_each_config_dir__${__WM_BIN_ID__}`

Original functions are still available for calling explicitly if combined effect is needed, see example in labwc plugin.

Example:

    #!/bin/false

    # function to make arbitrary actions before loading environment
    quirks__my_cool_wm() {
      # here additional vars can be set or unset
      export I_WANT_THIS_IN_SESSION=yes
      unset I_DO_NOT_WANT_THAT
      # or prepare a config for compositor
      # or set a var to modify what sourcing wayland-session-env, wayland-session-env-${__WM_ID__}
      # in the next stage will do
      ...
    }

    in_each_config_dir_reversed__my_cool_wm() {
      # custom mechanism for loading of env files (or a stub)
      # replaces standard function, but we want it also
      # so call it explicitly
      in_each_config_dir_reversed "$1"
      # and additionally source our file
      source_file "${1}/${__WM_ID__}/env"
    }

## Compliments

Inspired by and adapted some techniques from:

- [sway-services](https://github.com/xdbob/sway-services)
- [sway-systemd](https://github.com/alebastr/sway-systemd)
- [sway](https://github.com/swaywm/sway)
- [Presentation by Martin Pitt](https://people.debian.org/~mpitt/systemd.conf-2016-graphical-session.pdf)
