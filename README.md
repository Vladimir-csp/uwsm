# Universal Wayland Session Manager

Provides graphical session with environment management, XDG autostart support, and clean shutdown by
wrapping standalone Wayland compositors into a set of systemd units.

WIP(ish). The main structure of subcommands and features is more or less settled and will likely
not receive any drastic changes unless some illuminative idea comes by.
Nonetheless, keep an eye for commits with `[Breaking]` messages.

(!) v0.12 changed `wayland-session` name to `uwsm`. This affects names of executables, plugin dirs,
and log identifiers. See related installation [section](#1-executables-and-plugins).

(!) v0.13 added python-dbus dependency

Python dependencies:

- xdg
- dbus

## Concepts and features

<details><summary>
Uses systemd units and dependencies for startup, operation, and shutdown.
</summary>

  - Binds to the basic [structure](https://systemd.io/DESKTOP_ENVIRONMENTS/#pre-defined-systemd-units)
    of `graphical-session-pre.target`, `graphical-session.target`, `xdg-desktop-autostart.target`.
  - Aadds custom nested slices `app-graphical.slice`, `background-graphical.slice`, `session-graphical.slice`
    to put apps in and terminate them cleanly on exit.
  - Provides convenient way of [launching apps to those slices](https://systemd.io/DESKTOP_ENVIRONMENTS/#xdg-standardization-for-applications).
</details>

<details><summary>
Systemd units are treated with hierarchy and universality in mind.
</summary>

  - Templated units with specifiers.
  - Named from common to specific where possible.
  - Allowing for high-level `name-.d` drop-ins.
</details>

<details><summary>
Compositor-specific behavior is adjustable by plugins. Currently included: `sway`, `wayfire`, `labwc`, `Hyprland`.
</summary>

</details>

<details><summary>
Idempotently (well, best-effort-idempotently) handles environment.
</summary>

  - On startup environment is prepared by:
    - sourcing shell profile
    - sourcing `uwsm-env`, `uwsm-env-${desktop}` files
      from each dir of reversed `${XDG_CONFIG_HOME}:${XDG_CONFIG_DIRS}` (in increasing priority),
      where `${desktop}` is each item of `${XDG_CURRENT_DESKTOP}`, lowercased
  - Difference between environment state before and after preparation is exported into systemd user manager
    and dbus activation environment
  - On shutdown previously exported variables are unset from systemd user manager
    (dbus activation environment does not support unsetting, so those vars are emptied instead (!))
  - Lists of variables for export and cleanup are determined algorithmically by:
    - comparing environment before and after preparation procedures
    - boolean operations with predefined lists
</details>

<details><summary>
Can work with Desktop entries from `wayland-sessions` in XDG data hierarchy and/or be included in them.
</summary>

  - Actively select and launch compositor from Desktop entry (which is used as compositor instance ID):
    - Data taken from entry (Can be amended or overridden via cli arguments):
      - `Exec` for argument list
      - `DesktopNames` for `XDG_CURRENT_DESKTOP` and `XDG_SESSION_DESKTOP`
      - `Name` and `Comment` for unit `Description`
    - Entries can be overridden, masked or added in `${XDG_DATA_HOME}/wayland-sessions/`
    - Optional interactive selector (requires whiptail), choice is saved in `${XDG_CONFIG_HOME}/uwsm-default-id`
    - Desktop entry [actions](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s11.html) are supported
  - Be launched via a Desktop entry by a login/display manager.
</details>

<details><summary>
Can run with arbitrary compositor command line (saved as a unit drop-in).
</summary>
</details>

<details><summary>
Provides better control of XDG autostart apps.
</summary>

  - XDG autostart services (`app-*@autostart.service` units) are placed into `app-graphical.slice`
    that receives stop action before compositor is stopped.
  - Can be mass-controlled via stopping and starting `wayland-session-xdg-autostart@${compositor}.target`
</details>

<details><summary>
Tries best to shutdown session cleanly via a net of dependencies between units.
</summary>
</details>

<details><summary>
Provides helpers for various operations.
</summary>

  - Finalizing service startup (compositor service unit uses `Type=notify`) and exporting variables set by compositor
  - Launching applications as scopes or services in proper slices
    - desktop entries or plain executables are supported
    - support for launching a terminal/in terminal
    - flexible unit metadata support
  - Checking conditions for launch at login (for integration into login shell profile)
</details>

## Installation

### 1. Executables and plugins

Try `install.sh` (see `--help`).

Or to do it manually:

Put `uwsm` executable somewhere in `$PATH` (should also be searchable by systemd user manager).

- The executable can be renamed, and **it affects plugin dir and config file names (!)**, also log identifiers.
- Executable name does **not** affect unit names, since `wayland-`, `wayland-session-` are valid systemd drop-in globs.

Put `uwsm-plugins` dir somewhere in `${HOME}/.local/lib:/usr/local/lib:/usr/lib:/lib` (`UWSM_PLUGIN_PREFIX_PATH`)
as `${executable}-plugins` (corresponding to the executable above).

The rest of the manual will refer to default `uwsm` name.

Optional `uuctl` tool for managing user units (services and scopes) with dmenu-style menus can also be put in `$PATH`.

### 2. Vars set by compositor and startup notification

Ensure your compositor runs `uwsm finalize` at startup. Feed any environment variable names to be exported
to systemd user environment

<details><summary>
Details
</summary>

- It fills systemd and dbus environments with essential vars set by compositor: `WAYLAND_DISPLAY`, `DISPLAY`
- Any other vars can be given as arguments by name.
- Any exported variables are also added to cleanup list.
- If environment export is successful, it signals compositor service readiness,
  so `graphical-session.target` can properly be declared reached. If this stage fails, the compositor will be terminated in 10 seconds.
</details>

Example snippet for sway config:

`exec exec uwsm finalize SWAYSOCK I3SOCK XCURSOR_SIZE XCURSOR_THEME`

### 3. Applications and Slices

To properly put applications in `app-graphical.slice` (or like), Configure application launching in compositor via:

    uwsm app -- {executable|entry.desktop[:action]} [args ...]

When app launching is properly configured, compositor service itself can be placed in `session.slice` by either:

- Setting environment variable `UWSM_USE_SESSION_SLICE=true` before generating units. Best places to put this:
  - export in `~/.profile` before `uwsm` invocation
  - put in `~/.config/environment.d/*.conf` (see `man environment.d`)
- Adding `-S` argument to `uwsm start` subcommand.

<details><summary>
Background and details
</summary>

By default `uwsm` launhces compositor service in `app.slice` and all processes spawned by compositor
will be a part of `wayland-wm@${compositor}.service` unit. This works, but is not an optimal solution.

Systemd [documentation](https://systemd.io/DESKTOP_ENVIRONMENTS/#pre-defined-systemd-units)
recommends running compositors in `session.slice` and launch apps as scopes or services in `app.slice`.

`uwsm` provides convenient way of handling this, It generates special nested slices that will
also receive stop action ordered before `wayland-wm@${compositor}.service` shutdown:

- `app-graphical.slice`
- `background-graphical.slice`
- `session-graphical.slice`

`app-*@autostart.service` units are also modified to be started in `app-graphical.slice`.

To launch an app inside one of those slices, use:

`uwsm app [-s a|b|s|custom.slice] [-t scope|service] -- your_app [with args]`

Launching desktop entries via a [valid ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s02.html#desktop-file-id)
is also supported, (optionally with an [action ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s11.html) appended via ':'):

`uwsm app [-s a|b|s|custom.slice] [-t scope|service] -- your_app.desktop[:action] [with args]`

In this case args must be supported by the entry or its selected action according to
[XDG Desktop Entry Specification](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s07.html).

Always use `--` to disambiguate command line if any dashed arguments are intended for launched app.

Example snippets for sway config for launching apps:

Launch [proposed](https://gitlab.freedesktop.org/terminal-wg/specifications/-/merge_requests/3) default terminal:

`bindsym --to-code $mod+t exec exec uwsm app -T`

Fuzzel has a very handy launch-prefix option:

`bindsym --to-code $mod+r exec exec fuzzel --launch-prefix='uwsm app --' --log-no-syslog --log-level=warning`

Launch SpaceFM via a desktop entry:

`bindsym --to-code $mod+e exec exec uwsm app spacefm.desktop`

Featherpad desktop entry has "standalone-window" action:

`bindsym --to-code $mod+n exec exec uwsm app featherpad.desktop:standalone-window`

Unit type of launched apps can be controlled by `-t service|scope` argument or setting its default
via `UWSM_APP_UNIT_TYPE` env var.
</details>

## Operation

### Syntax and behavior

`-h|--help` option is available for `uwsm` and all of its subcommands.

Basics:

    uwsm start [options] -- ${compositor} [arguments]

Always use `--` to disambiguate command line if any dashed arguments are intended for launched compositor.

`${compositor}` can be an executable or a valid [desktop entry ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s02.html#desktop-file-id)
(optionally with an [action ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s11.html) appended via ':'), or one of special values: `select|default`

Optional parameters to provide more metadata:

- `-[a|e]D DesktopName1[:DesktopMame2:...]`: append (`-a`) or exclusively set (`-e`) `${XDG_CURRENT_DESKTOP}`
- `-N Name`
- `-C "Compositor description"`

Arguments and metadata are stored in specifier unit drop-ins if needed.

<details><summary>
Some details
</summary>

    uwsm start [-[a|e]D DesktopName1[:DesktopMame2:...]] [-N Name] [-C "Compositor description"] -- ${compositor} [with "any complex" --arguments]

If `${compositor}` is a desktop entry ID, `uwsm` will get desktop entry from `wayland-sessions` data hierarchy,
`Exec` will be used for command line, and `DesktopNames` will fill `$XDG_CURRENT_DESKTOP`, `Name` and `Comment` will go to units' descriptons.

Arguments provided on command line are appended to the command line of session desktop entry (unlike application entries),
no argument processing is done (Please [file a bug report](https://github.com/Vladimir-csp/uwsm/issues/new/choose)
if you encounter any wayland-sessions desktop entry with `%`-fields which would require this behavior to be altered).

If you want to customize compositor execution provided with a desktop entry, copy it to
`~/.local/share/wayland-sessions/` and change to your liking, including adding [actions](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s11.html).

If `${compositor}` is `select` or `default`, `uwsm` invokes a menu to select desktop entries available in
`wayland-sessions` data hierarchy (including their actions). Selection is saved, previous selection is highlighted
(or launched right away in case of `default`). Selected entry is used as instance ID.

There is also a separate `select` action (`uwsm select`) that only selects and saves default `${compositor}`
and does nothing else, which is handy for seamless shell profile integration.
</details>

When started, `uwsm` will wait while wayland session is running, and terminate session if
is itself interrupted or terminated.

### Where to launch from

#### Shell profile integration

To launch automatically after login on virtual console 1, if systemd is at `graphical.target`,
add this to shell profile:

    if uwsm check may-start && uwsm select; then
    	exec uwsm start default
    fi

`check may-start` checker subcommand, among other things, **screens for being in interactive login shell,
which is essential**, since profile sourcing can otherwise lead to nasty loops.

`select` shows whiptail menu to select default desktop entry from `wayland-sessions`. At this point one can cancel
and continue to the normal login shell.

`start default` launches the previously selected default compositor.

`exec` in shell profile causes `uwsm` to replace login shell, binding it to user's login session.

#### From display manager

To launch uwsm from a display/login manager, `uwsm` can be used inside desktop entries.
Example `/usr/local/share/wayland-sessions/my-compositor.desktop`:

    [Desktop Entry]
    Name=My compositor (with UWSM)
    Comment=My cool compositor
    Exec=uwsm start -N "My compositor" -D mycompositor -C "My cool compositor" mywm
    DesktopNames=mycompositor
    Type=Application

Things to keep in mind:

- For consistency, command line arguments should mirror the keys of the entry
- Command in `Exec=` should start with `uwsm`
- It should not launch a desktop entry, only an executable.

Potentially such entries may be found and used by `uwsm` itself, i.e. in shell profile integration
situation, or when launched manually. Following the principles above ensures `uwsm` will properly
recognize itself and parse requested arguments inside the entry without any side effects.

Testing and feedback is needed.

### How to stop

Either of:

- `loginctl terminate-user ""` (this ends all login sessions and units of current user,
  good for resetting everything including runtime units)
- `loginctl terminate-sesion "$XDG_SESSION_ID"` (this ends current login session.
  Empty argument will only work if loginctl is called from session scope)
- `uwsm stop` (effectively the same as previous one due to shell binding)
- `systemctl --user stop wayland-session@*.service` (effectively the same as previous one)

## Longer story, tour under the hood

Some extended examples and partial recreation of some behaviors via excessive shell code, just for deeper explanation.

<details><summary>
Dive
</summary>

### Start and bind

(At least for now) units are generated by the script.

Run `uwsm start -o ${compositor}` to populate `${XDG_RUNTIME_DIR}/systemd/user/` with them and do
nothing else (`-o`).

Any remainder arguments are appended to compositor argument list (even when `${compositor}` is a desktop entry).
Use `--` to disambigue:

`uwsm start -o -- ${compositor} with "any complex" --arguments`

Desktop entries can be overridden or added in `${XDG_DATA_HOME}/wayland-sessions/`.

Basic set of generated units:

- templated targets boud to stock systemd user-level targets
  - `wayland-session-pre@.target`
  - `wayland-session@.target`
  - `wayland-session-xdg-autostart@.target`
- templated services
  - `wayland-wm-env@.service` - environment preloader service
  - `wayland-wm@.service` - main compositor service
  - `wayland-wm-app-daemon.service` - fast app command generator
- slices for apps nested in stock systemd user-level slices
  - `app-graphical.slice`
  - `background-graphical.slice`
  - `session-graphical.slice`
- tweaks
  - `wayland-wm-env@${compositor}.service.d/custom.conf`, `wayland-wm@${compositor}.service.d/custom.conf` -
    if arguments and/or various names were given on command line, they go here.
  - `app-@autostart.service.d/slice-tweak.conf` - assigns XDG autostart apps to `app-graphical.slice`
- shutdown and cleanup target
  - `wayland-session-shutdown.target` - conflicts with operational units. Triggered by the end of `wayland-wm*.service` units
    for more robust cleanup, including on failures. But can also be called manually for shutdown.

After units are generated, compositor can be started by: `systemctl --user start wayland-wm@${compositor}.service`

Add `--wait` to hold terminal until session ends.

`exec` it from login shell to bind to login session:

`exec systemctl --user start --wait wayland-wm@${compositor}.service`

Still if login session is terminated, wayland session will continue running, most likely no longer being accessible.

To also bind it the other way around, shell traps are used:

`trap "if systemctl --user is-active -q wayland-wm@${compositor}.service ; then systemctl --user --stop wayland-wm@${compositor}.service ; fi" INT EXIT HUP TERM`

This makes the end of login shell also be the end of wayland session.

When `wayland-wm-env@.service` is started during `graphical-session-pre.target` startup,
`uwsm aux prepare-env ${compositor}` is launched (with shared set of custom arguments).

It runs shell code to prepare environment, that sources shell profile, `uwsm-env*` files,
anything that plugins dictate. Environment state at the end of shell code is given back to the main process.
`uwsm` is also smart enough to find login session associated with current TTY
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

`uwsm finalize [VAR [VAR2...]]` runs:

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

When `wayland-wm-env@${compositor}.service` is stopped, `uwsm aux cleanup-env` is launched.
It looks for **any** cleanup files (`env_names_for_cleanup_*`) in runtime dir. Listed variables,
plus `varnames.always_cleanup` minus `varnames.never_cleanup`
are emptied in dbus activation environment and unset from systemd user manager environment.

When no compositor is running, units can be removed (`-r`) by `uwsm stop -r`.

Add compositor to `-r` to remove only customization drop-ins: `uwsm stop -r ${compositor}`.

### Profile integration

This example does the same thing as `check may-start` + `start` subcommand combination described earlier:
starts wayland session automatically upon login on tty1 if system is in `graphical.target`

**Screening for being in interactive login shell here is essential** (`[ "${0}" != "${0#-}" ]`).
`wayland-wm-env@${compositor}.service` sources profile, which has a potential for nasty loops if run
unconditionally. Other conditions are a recommendation:

    MY_COMPOSITOR=sway
    if [ "${0}" != "${0#-}" ] &&
       [ "$XDG_VTNR" = "1" ] &&
       systemctl is-active -q graphical.target &&
       ! systemctl --user is-active -q wayland-wm@*.service
    then
        uwsm start -o ${MY_COMPOSITOR}
        trap "if systemctl --user is-active -q wayland-wm@${MY_COMPOSITOR}.service ; then systemctl --user --stop wayland-wm@${MY_COMPOSITOR}.service ; fi" INT EXIT HUP TERM
        echo Starting ${MY_COMPOSITOR} compositor
        systemctl --user start --wait wayland-wm@${MY_COMPOSITOR}.service &
        wait
        exit
    fi
</details>

## Compositor-specific actions

Shell plugins provide compositor-specific functions during environment preparation.

Named `${__WM_BIN_ID__}.sh.in`, they should only contain specifically named functions.

`${__WM_BIN_ID__}` is derived from the item 0 of compositor command line by applying `s/(^[^a-zA-Z]|[^a-zA-Z0-9_])+/_/`

It is used as plugin id and suffix in function names.

Variables available to plugins:

- `__WM_ID__` - compositor ID, effective first argument of `start`.
- `__WM_ID_UNIT_STRING__` - compositor ID escaped for systemd unit name.
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
  loads `uwsm-env`, `uwsm-env-${desktop}` files
- `process_config_dirs` - called by `load_wm_env`,
  iterates over XDG_CONFIG hierarchy (decreasing priority)
- `in_each_config_dir` - called by `process_config_dirs` for each config dir, does nothing ATM
- `source_file` - sources `$1` file, providing messages for log.

See code inside `uwsm` for more auxillary funcions.

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
      # or set a var to modify what sourcing uwsm-env, uwsm-env-${__WM_ID__}
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

Special thanks to @skewballfox for help with python and pointing me to useful tools.
