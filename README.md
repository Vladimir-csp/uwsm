# Universal Wayland Session Manager

Wraps standalone Wayland compositors into a set of Systemd units on the fly.
This provides robust session management including environment, XDG autostart
support, bi-directional binding with login session, and clean shutdown.

For compositors this is an opportunity to offload Systemd integration and
session/XDG autostart management in Systemd-managed environments.

> [!IMPORTANT]
> This project is currently in a stable phase with a slow-burning refactoring.
> Although no drastic changes are planned, keep an eye for commits with breaking
> changes, indicated by an exclamation point (e.g. `fix!: ...`, `chore!: ...`,
> `feat!: ...`, etc.).

> [!NOTE]
> It is highly recommended to use
> [dbus-broker](https://github.com/bus1/dbus-broker) as the D-Bus daemon
> implementation. Among other benefits, it reuses the systemd activation
> environment instead of having its own separate one. This simplifies
> environment management and allows proper cleanup. The separate activation
> environment of the reference D-Bus implementation doesn't allow unsetting
> vars, so they're set to an empty string instead, as a best effort cleanup. The
> only way to properly clean up the environment in this case is to run
> `loginctl terminate-user ""`.

![uwsm select (via whiptail)](uwsm_select.png)

## Concepts and features

<details><summary>
Uses systemd units and dependencies for startup, operation, and shutdown.
</summary>

- Binds to the basic
  [structure](https://systemd.io/DESKTOP_ENVIRONMENTS/#pre-defined-systemd-units)
  of `graphical-session-pre.target`, `graphical-session.target`,
  `xdg-desktop-autostart.target`.
- Adds custom nested slices `app-graphical.slice`,
  `background-graphical.slice`, `session-graphical.slice` to put apps in and
  terminate them cleanly on exit.
- Provides convenient way of
  [launching apps to those slices](https://systemd.io/DESKTOP_ENVIRONMENTS/#xdg-standardization-for-applications).

</details>

<details><summary>
Systemd units are treated with hierarchy and universality in mind.
</summary>

- Templated units with specifiers.
- Named from common to specific where possible.
- Allowing for high-level `name-.d` drop-ins.

</details>

<details><summary>
Bi-directional binding between login session and graphical session.
</summary>

Using `waitpid` utility (or a built-in shim) together with native systemd
mechanisms, uwsm binds lifetime of a login session (`session-N.scope` system
unit) to graphical session (a set of user units) and vice versa.

</details>

<details><summary>
Compositor-specific behavior is adjustable by plugins.
</summary>

Currently included:

- `sway`
- `wayfire`
- `labwc`
- `hyprland`

</details>

<details><summary>
Idempotently (well, best-effort-idempotently) handles environment.
</summary>

- On startup a specialized unit prepares environment by:
  - sourcing shell profile
  - sourcing `uwsm-env`, `uwsm-env-${desktop}` files from each dir of reversed
    `${XDG_CONFIG_HOME}:${XDG_CONFIG_DIRS}` (in increasing priority), where
    `${desktop}` is each item of `${XDG_CURRENT_DESKTOP}`, lowercased
- Difference between environment state before and after preparation is exported
  into systemd user manager (and dbus activation environment if reference dbus
  implementation is used)
- On shutdown previously exported variables are unset from systemd user manager
  (activation environment of reference dbus daemon does not support unsetting,
  so those vars are emptied instead (!))
- Lists of variables for export and cleanup are determined algorithmically by:
  - comparing environment before and after preparation procedures
  - boolean operations with predefined lists
  - manually exported vars by `uwsm finalize` action

Summary of where to put a user-level var:
- For entire user's context: define in `${XDG_CONFIG_HOME}/environment.d/*.conf` (see `man 5 environment.d`)
- For login session context: export in `~/.profile` (may have caveats, see your shell's manual)
- For uwsm-managed graphical session: export in `${XDG_CONFIG_HOME}/uwsm-env`
- For uwsm-managed graphical session of specific compositor: export in `${XDG_CONFIG_HOME}/uwsm-env-${desktop}`

Also for convenience environment preloader defines `IN_UWSM_ENV_PRELOADER=true`
variable, which can be probed from shell profile to do things conditionally.

</details>

<details><summary>
Can work with Desktop entries from `wayland-sessions` in XDG data hierarchy and/or be included in them.
</summary>

- Actively select and launch compositor from Desktop entry (which is used as
  compositor instance ID):
  - Data taken from entry (Can be amended or overridden via cli arguments):
    - `Exec` for argument list
    - `DesktopNames` for `XDG_CURRENT_DESKTOP` and `XDG_SESSION_DESKTOP`
    - `Name` and `Comment` for unit `Description`
  - Entries can be overridden, masked or added in
    `${XDG_DATA_HOME}/wayland-sessions/`
  - Optional interactive selector (requires whiptail), choice is saved in
    `${XDG_CONFIG_HOME}/uwsm-default-id`
  - Desktop entry
    [actions](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s11.html)
    are supported
- Be launched via a Desktop entry by a login/display manager.

</details>

<details><summary>
Can run with arbitrary compositor command line, or take it (along with other data) from desktop entries (saved as a unit drop-in).
</summary>

```
wayland-wm-env@${compositor}.service.d/50_custom.conf
wayland-wm@${compositor}.service.d/50_custom.conf
```

</details>

<details><summary>
Provides better control of XDG autostart apps.
</summary>

- XDG autostart services (`app-*@autostart.service` units) are placed into
  `app-graphical.slice` that receives stop action before compositor is stopped.
- Can be mass-controlled via stopping and starting
  `wayland-session-xdg-autostart@${compositor}.target`

</details>

<details><summary>
Tries best to shutdown session cleanly via a net of dependencies between units.
</summary>

All managed transient files (in `/run/user/${UID}/systemd/user`):

```
background-graphical.slice
app-graphical.slice
session-graphical.slice
app-@autostart.service.d/slice-tweak.conf
wayland-session-pre@.target
wayland-session-shutdown.target
wayland-session-xdg-autostart@.target
wayland-session@.target
wayland-wm-app-daemon.service
wayland-wm-env@.service
wayland-wm-env@${compositor}.service.d/50_custom.conf
wayland-wm@.service
wayland-wm@${compositor}.service.d/50_custom.conf
wayland-session-bindpid@.target
```

See [Longer story](#longer-story-tour-under-the-hood) section below for
descriptions.

</details>

<details><summary>
Provides helpers and tools for various operations.
</summary>

- `uwsm finalize`: for finalizing service startup (compositor service unit uses
  `Type=notify`) and exporting variables set by compositor
- `uwsm check may-start`: for checking conditions for launch at login (for
  integration into login shell profile)
- `uwsm app`: for launching applications as scopes or services in proper slices
  - desktop entries or plain executables are supported
  - support for launching a terminal/in terminal
    ([proposed xdg-terminal-exec](https://gitlab.freedesktop.org/terminal-wg/specifications/-/merge_requests/3))
  - flexible unit metadata support
- `uwsm-app`: a simple and fast shell client to app-daemon feature of uwsm, a
  drop-in replacement of `uwsm app`. The daemon (started on-demand) handles
  finding requested desktop entries, parsing and generation of commands for
  client to execute. This avoids the overhead of repeated python startup and
  increases app launch speed.
- `uuctl`: graphical (via dmenu-like menus) tool for managing user units.
- `fumon`: background service for notifying about failed units

</details>

## Installation

### 1. Building and installing

Checkout the last version-tagged commit. Untagged commits are WIP.

<details><summary>
Building and installing the python project directly.
</summary>

```
meson setup --prefix=/usr/local -Duuctl=enabled -Dfumon=enabled -Duwsm-app=enabled build
meson install -C build
```

The example enables optional tools `uuctl`, `fumon`, and `uwsm-app` available in
this project (see _helpers and tools_ spoiler in
[concepts section](#concepts-and-features) above).

</details>

<details><summary>
Building and installing a deb package.
</summary>

Read and run `./build-deb.sh -i`

Alternatively, 
```
IFS='()' read -r _ current_version _ < debian/changelog
sudo apt install devscripts
mk-build-deps
sudo apt install --mark-auto ./uwsm-build-deps_${current_version}_all.deb
dpkg-buildpackage -b -tc --no-sign
sudo apt install ../uwsm_${current_version}_all.deb
```

</details>

<details><summary>
Arch AUR package.
</summary>

https://aur.archlinux.org/packages/uwsm

</details>

<details><summary>
Nix flake/derivation.
</summary>

https://github.com/minego/uwsm.nix

</details>

Runtime dependencies:
- python modules:
    - xdg (pyxdg)
    - dbus (dbus_python)
- `waitpid` (optional, but recommended for resources; from `util-linux` or
  `util-linux-extra` package)
- `whiptail` (optional, for `select` feature; from `whiptail` or `libnewt`
   package)
- a dmenu-like menu (optional; for `uuctl` script), supported:
    - `fuzzel`
    - `wofi`
    - `rofi`
    - `tofi`
    - `bemenu`
    - `wmenu`
    - `dmenu`
- `notify-send` (optional, for feedback messages; `libnotify-bin` or `libnotify`
  package)

### 2. Service startup notification and vars set by compositor

Ensure your compositor runs `uwsm finalize` command at the end of its startup.
If compositor sets any useful environment variables, list their names as
arguments.

<details><summary>
Details
</summary>

- It fills systemd and dbus environments with essential vars set by compositor:
  `WAYLAND_DISPLAY`, `DISPLAY`
- Any additional vars can be given as arguments by name or listed in
 `UWSM_FINALIZE_VARNAMES` var, which is also pre-filled by plugins.
- Undefined vars are silently ignored.
- Any exported variables are also added to cleanup list.
- If environment export is successful, it signals compositor service readiness,
  so `graphical-session.target` can properly be declared reached. If this stage
  fails, the compositor will be terminated in 10 seconds.

</details>

Example snippet for sway config (these vars are already covered by sway plugin
by adding them to `UWSM_FINALIZE_VARNAMES` var, listed here just for clearness):

`exec exec uwsm finalize SWAYSOCK I3SOCK XCURSOR_SIZE XCURSOR_THEME`

### 3. Applications and Slices

To properly put applications in `app-graphical.slice` (or like), Configure
application launching in compositor via:

```
uwsm app -- {executable|entry.desktop[:action]} [args ...]
```

When app launching is properly configured, compositor service itself can be
placed in `session.slice` by either:

- Setting environment variable `UWSM_USE_SESSION_SLICE=true` before generating
  units. Best places to put this:
  - export in `~/.profile` before `uwsm` invocation
  - put in `~/.config/environment.d/*.conf` (see `man environment.d`)
- Adding `-S` argument to `uwsm start` subcommand.

<details><summary>
Background and details
</summary>

By default `uwsm` launhces compositor service in `app.slice` and all processes
spawned by compositor will be a part of `wayland-wm@${compositor}.service` unit.
This works, but is not an optimal solution.

Systemd
[documentation](https://systemd.io/DESKTOP_ENVIRONMENTS/#pre-defined-systemd-units)
recommends running compositors in `session.slice` and launch apps as scopes or
services in `app.slice`.

`uwsm` provides convenient way of handling this: it generates special nested
slices that will also receive stop action ordered before
`wayland-wm@${compositor}.service` shutdown:

- `app-graphical.slice`
- `background-graphical.slice`
- `session-graphical.slice`

`app-*@autostart.service` units are also modified to be started in
`app-graphical.slice`.

To launch an app inside one of those slices, use:

`uwsm app [-s a|b|s|custom.slice] [-t scope|service] -- your_app [with args]`

Launching desktop entries via a
[valid ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s02.html#desktop-file-id)
is also supported, (optionally with an
[action ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s11.html)
appended via ':'):

`uwsm app [-s a|b|s|custom.slice] [-t scope|service] -- your_app.desktop[:action] [with args]`

In this case args must be supported by the entry or its selected action
according to
[XDG Desktop Entry Specification](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s07.html).

Specifying paths to executables or desktop entry files is also supported.

Always use `--` to disambiguate command line if any dashed arguments are
intended for the app being launched.

Example snippets for sway config for launching apps:

Launch
[proposed](https://gitlab.freedesktop.org/terminal-wg/specifications/-/merge_requests/3)
default terminal:

`bindsym --to-code $mod+t exec exec uwsm app -T`

Fuzzel has a very handy launch-prefix option:

`bindsym --to-code $mod+r exec exec fuzzel --launch-prefix='uwsm app --' --log-no-syslog --log-level=warning`

Launch SpaceFM via a desktop entry:

`bindsym --to-code $mod+e exec exec uwsm app spacefm.desktop`

Featherpad desktop entry has "standalone-window" action:

`bindsym --to-code $mod+n exec exec uwsm app featherpad.desktop:standalone-window`

Unit type of launched apps can be controlled by `-t service|scope` argument or
setting its default via `UWSM_APP_UNIT_TYPE` env var.

</details>

## Operation

### Syntax and behavior

`-h|--help` option is available for `uwsm` and all of its subcommands.

Basics:

```
uwsm start [options] -- ${compositor} [arguments]
```

Always use `--` to disambiguate command line if any dashed arguments are
intended for launched compositor.

`${compositor}` can be an executable or a valid
[desktop entry ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s02.html#desktop-file-id)
(optionally with an
[action ID](https://specifications.freedesktop.org/desktop-entry-spec/latest/ar01s11.html)
appended via ':'), or one of special values: `select|default`

Optional parameters to provide more metadata:

- `-[a|e]D DesktopName1[:DesktopMame2:...]`: append (`-a`) or exclusively set
  (`-e`) `${XDG_CURRENT_DESKTOP}`
- `-N Name`
- `-C "Compositor description"`

Arguments and metadata are stored in specifier unit drop-ins if needed.

`uwsm start ...` command will wait until graphical session ends, also holding
open the login session it resides in. Graphical session will also deactivate if
process that started it ends.

<details><summary>
Some details
</summary>

```
uwsm start [-[a|e]D DesktopName1[:DesktopMame2:...]] [-N Name] [-C "Compositor description"] -- ${compositor} [with "any complex" --arguments]
```

If `${compositor}` is a desktop entry ID, `uwsm` will get desktop entry from
`wayland-sessions` data hierarchy, `Exec` will be used for command line, and
`DesktopNames` will fill `$XDG_CURRENT_DESKTOP`, `Name` and `Comment` will go to
units' description.

Arguments provided on command line are appended to the command line from
session's desktop entry (unlike application entries), no argument processing is
done (Please
[file a bug report](https://github.com/Vladimir-csp/uwsm/issues/new/choose) if
you encounter any wayland-sessions desktop entry with `%`-fields which would
require this behavior to be altered).

If you want to customize compositor execution provided with a desktop entry,
copy it to `~/.local/share/wayland-sessions/` and change to your liking,
including adding
[actions](https://specifications.freedesktop.org/desktop-entry-spec/1.5/ar01s11.html).

If `${compositor}` is `select` or `default`, `uwsm` invokes a menu to select
desktop entries available in `wayland-sessions` data hierarchy (including their
actions). Selection is saved, previous selection is highlighted (or launched
right away in case of `default`). Selected entry is used as instance ID.

There is also a separate `select` action (`uwsm select`) that only selects and
saves default `${compositor}` and does nothing else, which is handy for seamless
shell profile integration.

Things `uwsm start ...` will do:
- Prepare unit structure in runtime directory.
- Fork a process protected from `TERM` and `HUP` signals that will find future
  compositor unit's `MainPID` and wait for it to end, ensuring login session is
  kept open until graphical session ends.
- Start `wayland-session-bindpid@.service` unit pointing to `uwsm`'s own PID to
  rig graphical session shutdown in case `uwsm` (or login session) ends.
- Finally, replace itself with `systemctl` command which will actually start the
  compositor unit and wait while wayland session is running.

</details>

### Where to launch from

#### Shell profile integration

To launch automatically after login on virtual console 1, if systemd is at
`graphical.target`, add this to shell profile:

```
if uwsm check may-start && uwsm select; then
	exec systemd-cat -t uwsm_start uwsm start default
fi
```

`uwsm check may-start` checker subcommand, among other things, **screens for
being in interactive login shell, which is essential**, since profile sourcing
can otherwise lead to nasty loops.

`uwsm start select` shows whiptail menu to select default desktop entry from
`wayland-sessions` directories. At this point one can cancel and continue with
the normal login shell.

`exec` in shell profile causes `uwsm` (via `systemd-cat`) to replace login
shell, binding it to user's login session.

`systemd-cat -t uwsm_start` part is optional, it executes the command given to
it (`uwsm`) with its stdout and stderr connected to the systemd journal, tagged
with identifier `uwsm_start`. Otherwise it might be hard to see the output.

`uwsm start default` launches the previously selected default compositor.

#### From a display manager

To launch uwsm from a display/login manager, `uwsm` can be used inside desktop
entries. Example `/usr/local/share/wayland-sessions/my-compositor.desktop`:

```
[Desktop Entry]
Name=My compositor (with UWSM)
Comment=My cool compositor
Exec=uwsm start -N "My compositor" -D mycompositor -C "My cool compositor" mywm
DesktopNames=mycompositor
Type=Application
```

Things to keep in mind:

- For consistency, command line arguments should mirror the keys of the entry
- Command in `Exec=` should start with `uwsm start`
- It should not point to itself (as a combination of Desktop Entry ID and Action
  ID)
- It should not point to a Desktop Entry ID and Action ID that also uses `uwsm`

Potentially such entries may be found and used by `uwsm` itself, i.e. in shell
profile integration situation, or when launched manually. Following the
principles above ensures `uwsm` will properly recognize itself and parse
requested arguments inside the entry without any side effects.

Testing and feedback is needed.

Aternatively, if a display manager supports wrapper commands/scripts, `uwsm`
can be inserted there to receive either Entry and Action IDs, or parsed command
line.

### How to stop

Either of:

- `loginctl terminate-user ""` (this ends all login sessions and units of
  current user, good for resetting everything, including runtime units,
  environments, etc.)
- `loginctl terminate-sesion "$XDG_SESSION_ID"` (this ends current login
  session, uwsm in this session will bring down graphical session units before
  exiting. Empty argument will only work if loginctl is called from session
  scope itself)
- `uwsm stop` (brings down graphical session units. Login session will end if
  `uwsm start` replaces login shell)
- `systemctl --user stop wayland-wm@*.service` (effectively the same as previous
  one)

## Longer story, tour under the hood

Some extended examples and partial recreation of some behaviors via excessive
shell code, just for deeper explanation.

<details><summary>
Dive
</summary>

### Start and bind

(At least for now) units are generated by the script.

Run `uwsm start -o ${compositor}` to populate `${XDG_RUNTIME_DIR}/systemd/user/`
with them and do nothing else (`-o`).

Any remainder arguments are appended to compositor argument list (even when
`${compositor}` is a desktop entry). Use `--` to disambigue:

`uwsm start -o -- ${compositor} with "any complex" --arguments`

Desktop entries can be overridden or added in
`${XDG_DATA_HOME}/wayland-sessions/`.

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
  - `wayland-wm-env@${compositor}.service.d/50_custom.conf`,
    `wayland-wm@${compositor}.service.d/50_custom.conf` - if arguments and/or
    various names, path to executable were given on command line, they go here.
  - `app-@autostart.service.d/slice-tweak.conf` - assigns XDG autostart apps to
    `app-graphical.slice`
- shutdown and cleanup units
  - `wayland-session-bindpid@.service` - starts `waitpid` utility for a given
    PID. Invokes `wayland-session-shutdown.target` when deactivated.
    `uwsm start` starts this unit pointing to itself just before replacing
    itself with `systemctl` unit startup command.
  - `wayland-session-shutdown.target` - conflicts with operational units.
    Triggered by deactivation of `wayland-wm*@*.service` and
    `wayland-session-bindpid@*.service` units, both successful or failed. But
    can also be called manually for shutdown.

After units are generated, compositor can be started by:
`systemctl --user start wayland-wm@${compositor}.service`

But this would run it completely disconnected from a login session or any
process that started it. To fix that use `wayland-session-bindpid@.service` to
track PID of login shell (`$$`) and stop graphical session when it exits:

`systemctl --user start wayland-session-bindpid@$$.service`

Add `--wait` to hold the terminal until session ends, `exec` it to replace login
shell with `systemctl` invocation reusing its PID:

`exec systemctl --user start --wait wayland-wm@${compositor}.service`

This makes the end of login shell also be the end of wayland session and vice
versa.

When `wayland-wm-env@.service` is started during `graphical-session-pre.target`
startup, `uwsm aux prepare-env ${compositor}` is launched (with shared set of
custom arguments).

It runs shell code to prepare environment, that sources shell profile,
`uwsm-env*` files, anything that plugins dictate. Environment state at the end
of shell code is given back to the main process. `uwsm` is also smart enough to
find login session associated with current TTY and set `$XDG_SESSION_ID`,
`$XDG_VTNR`.

The difference between initial env (that is the state of activation environment)
and after all the sourcing and setting is done, plus `varnames.always_export`,
minus `varnames.never_export`, is added to activation environment of systemd
user manager and dbus.

Those variable names, plus `varnames.always_cleanup` minus
`varnames.never_cleanup` are written to a cleanup list file in runtime dir.

### Startup finalization

`wayland-wm@.service` uses `Type=notify` and waits for compositor to signal
started state. Activation environments will also need to receive essential
variables like `WAYLAND_DISPLAY` to launch graphical applications successfully.

`uwsm finalize [VAR [VAR2...]]` essentially performs action analogous to:

```
dbus-update-activation-environment --systemd WAYLAND_DISPLAY DISPLAY [VAR [VAR2...]]
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY [VAR [VAR2...]]
systemd-notify --ready
```

(`dbus-update-activation-environment` is skpped for `dbus-broker`)

Additional variable names are taken from `UWSM_FINALIZE_VARNAMES` var.

Only defined variables are used. Variables that are not blacklisted by
`varnames.never_cleanup` set are also added to cleanup list in the runtime dir.

### Stop

Just stop the main service:
`systemctl --user stop "wayland-wm@${compositor}.service"`, everything else will
stopped by systemd.

Wildcard `systemctl --user stop "wayland-wm@*.service"` will also work.

Or activate shutdown target: `systemctl --user start wayland-session-shutdown.target`

If an instance of `wayland-session-bindpid@.service` is active and pointing to a
PID in login session, this stop command also doubles as a logout command.

When `wayland-wm-env@${compositor}.service` is stopped, `uwsm aux cleanup-env`
is launched. It looks for **any** cleanup files (`env_names_for_cleanup_*`) in
runtime dir. Listed variables, plus `varnames.always_cleanup` minus
`varnames.never_cleanup` are emptied in dbus activation environment and unset
from systemd user manager environment.

When no compositor is running, units can be removed (`-r`) by `uwsm stop -r`.

Add compositor to `-r` to remove only customization drop-ins:
`uwsm stop -r ${compositor}`.

### Profile integration

This example does the same thing as `check may-start` + `start` subcommand
combination described earlier: starts wayland session automatically upon login
on tty1 if system is in `graphical.target`

**Screening for being in interactive login shell here is essential**
(`[ "${0}" != "${0#-}" ]`). `wayland-wm-env@${compositor}.service` sources
profile, which has a potential for nasty loops if run unconditionally. Other
conditions are a recommendation:

```
MY_COMPOSITOR=sway
if [ "${0}" != "${0#-}" ] &&
   ! systemctl --user is-active -q wayland-wm@*.service &&
   [ "$XDG_VTNR" = "1" ] &&
   {
       # wait while graphical.target is in startup queue
       while case "$(systemctl list-jobs --plain --no-legend --full graphical.target)" in
       *start*) true ;; *) false ;; esac; do
         sleep 1
       done
       systemctl is-active -q graphical.target
   }
then
    # generate units
    uwsm start -o ${MY_COMPOSITOR}
    # bind wayland session to login shell PID $$
    echo Starting ${MY_COMPOSITOR} compositor
    systemctl --user start wayland-session-bindpid@$$.service &&
    exec systemctl --user start --wait wayland-wm@${MY_COMPOSITOR}.service
fi
```

`uwsm start` also has a mechanism that holds the login session open until the
compositor unit is deactivated. It works by forking a process immune to `TERM`
and `HUP` signals inside login session. This process finds compositor unit's
`MainPID` and waits until it ends. This mechanism would be too complicated to
replicate in shell for purposes of this demonstration.

</details>

## Compositor-specific actions

Shell plugins provide compositor-specific functions during environment
preparation.

Named `${__WM_BIN_ID__}.sh`, they should only contain specifically named
functions.

`${__WM_BIN_ID__}` is derived from the item 0 of compositor command line by
applying `s/(^[^a-zA-Z]|[^a-zA-Z0-9_])+/_/` and converting to lower case.

It is used as plugin id and suffix in function names.

Variables available to plugins:

- `__WM_ID__` - compositor ID, effective first argument of `start`.
- `__WM_ID_UNIT_STRING__` - compositor ID escaped for systemd unit name.
- `__WM_BIN_ID__` - processed first item of compositor argv.
- `__WM_DESKTOP_NAMES__` - `:`-separated desktop names from `DesktopNames=` of
  entry and `-D` cli argument.
- `__WM_FIRST_DESKTOP_NAME__` - first of the above.
- `__WM_DESKTOP_NAMES_LOWERCASE__` - same as the above, but in lower case.
- `__WM_FIRST_DESKTOP_NAME_LOWERCASE__` - first of the above.
- `__WM_DESKTOP_NAMES_EXCLUSIVE__` - (`true`|`false`) indicates that
  `__WM_DESKTOP_NAMES__` came from cli argument and are marked as exclusive.
- `__OIFS__` - contains shell default field separator (space, tab, newline) for
  convenient restoring.

Standard functions:

- `load_wm_env` - standard function for loading env files
- `process_config_dirs_reversed` - called by `load_wm_env`, iterates over
  XDG_CONFIG hierarchy in reverse (increasing priority)
- `in_each_config_dir_reversed` - called by `process_config_dirs_reversed` for
  each config dir, loads `uwsm-env`, `uwsm-env-${desktop}` files
- `process_config_dirs` - called by `load_wm_env`, iterates over XDG_CONFIG
  hierarchy (decreasing priority)
- `in_each_config_dir` - called by `process_config_dirs` for each config dir,
  does nothing ATM
- `source_file` - sources `$1` file, providing messages for log.

See code inside `uwsm/main.py` for more auxillary funcions.

Functions that can be added by plugins, replacing standard funcions:

- `quirks__${__WM_BIN_ID__}` - called before env loading.
- `load_wm_env__${__WM_BIN_ID__}`
- `process_config_dirs_reversed__${__WM_BIN_ID__}`
- `in_each_config_dir_reversed__${__WM_BIN_ID__}`
- `process_config_dirs__${__WM_BIN_ID__}`
- `in_each_config_dir__${__WM_BIN_ID__}`

Original functions are still available for calling explicitly if combined effect
is needed.

Example:

```
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
  # add a var to be exported by uwsm finalize:
  UWSM_FINALIZE_VARNAMES="${UWSM_FINALIZE_VARNAMES}${UWSM_FINALIZE_VARNAMES:+ }ANOTHER_VAR1 ANOTHER_VAR2"
}

in_each_config_dir_reversed__my_cool_wm() {
  # custom mechanism for loading of env files (or a stub)
  # replaces standard function, but we want it also
  # so call it explicitly
  in_each_config_dir_reversed "$1"
  # and additionally source our file
  source_file "${1}/${__WM_ID__}/env"
}
```

## Compliments

Inspired by and adapted some techniques from:

- [sway-services](https://github.com/xdbob/sway-services)
- [sway-systemd](https://github.com/alebastr/sway-systemd)
- [sway](https://github.com/swaywm/sway)
- [Presentation by Martin Pitt](https://people.debian.org/~mpitt/systemd.conf-2016-graphical-session.pdf)

Special thanks to @skewballfox for help with python and pointing me to useful
tools.
