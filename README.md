# Universal Wayland Session Manager

Experimental tool that wraps any standalone Wayland WM into a set of systemd units to
provide graphical user session with environment management, XDG autostart support, clean shutdown.

WIP. Use at your onw risk. Breaking changes are being introduced. See commit messages.

**(!) v0.2 changed arguments order and introduced helper commands**.

## Concepts and features

- Maximum use of systemd units and dependencies for startup, operation, and shutdown
  - binds to basic structure of `graphical-session-pre.target`, `graphical-session.target`, `xdg-desktop-autostart.target`
  - adds custom slices `app-graphical.slice`, `background-graphical.slice`, `session-graphical.slice` to put apps in and terminate them cleanly
  - provides convenient way of launching apps to those slices
- Systemd units are treated with hierarchy and universality in mind:
  - templated units with specifiers
  - named from common to specific where possible
  - allowing for high-level `name-.d` drop-ins
- WM-specific behavior can be added by plugins
  - currently supported: sway, wayfire, labwc
- Idempotently (well, best-effort-idempotently) handle environment:
  - On startup environment is prepared by:
    - sourcing shell profile
    - sourcing common `wayland-session-env` files (from $XDG_CONFIG_DIRS, $XDG_CONFIG_HOME)
    - sourcing WM-specific `wayland-session-${wm}-env` files (from $XDG_CONFIG_DIRS, $XDG_CONFIG_HOME)
  - Difference between environment state before and after preparation is exported into systemd user manager and dbus activation environment
  - On shutdown variables that were exported are unset from systemd user manager (dbus activation environment does not support unsetting, sadly)
  - Lists of variables for export and cleanup are determined algorithmically by:
    - comparing environment before and after preparation procedures
    - boolean operations with predefined lists (tweakable by plugins)
- Can select WM from `wayland-sessions` desktop entries in XDG data hierarchy (requires python-xdg and whiptail)
  - WM desktop entries can be overridden or added in `${XDG_DATA_HOME}/wayland-sessions/`
- Can run with arbitrary WM command line
- Better control of XDG autostart apps:
  - XDG autostart services (`app-*@autostart.service` units) are placed into `app-graphical.slice` that receives stop action before WM is stopped.
  - Can be mass-controlled via stopping and starting `wayland-session-xdg-autostart@${wm}.target`
- Try best to shutdown session cleanly via a lot of dependencies between units
- Written in POSIX shell (a smidgen of masochism went into this code)
  - the only exception is `wayland-sessions` desktop entries support in `select` or `default` modes

## Installation

### 1. Executables and plugins

Put `wayland-session` executable somewhere in `$PATH`.

Put `wayland-session-plugins` dir somewhere in `/lib:/usr/lib:/usr/local/lib:${HOME}/.local/lib`

### 2. Vars set by WM and Startup notification

Ensure your WM runs `wayland-session finalize` at startup:

- it fills systemd and dbus environments with essential vars set by WM: `WAYLAND_DISPLAY`, `DISPLAY` (other vars can be given by names as arguments)
- if environment export is successful, it signals WM service readiness via `systemd-notify --ready`

Example snippet for sway config:

`exec exec wayland-session finalize SWAYSOCK I3SOCK XCURSOR_SIZE XCURSOR_THEME`

### 3. Slices

By default `wayland-session` launces WM service in `app.slice` and all processes spawned by WM will be
a part of `wayland-wm@${wm}.service` unit. This works, but is not an optimal solution.

Systemd documentation recommends running compositors in `session.slice` and launch apps as scopes in `app.slice`.

`wayland-session` provides convenient way of handling this.
It generates special nested slices that will also receive stop action ordered before `wayland-wm@${wm}.service` shutdown:

- `app-graphical.slice`
- `background-graphical.slice`
- `session-graphical.slice`

`app-*@autostart.service` and `xdg-desktop-portal-*.service` units are also modified to be started in `app-graphical.slice`.

To launch an app scoped inside one of those slices, use `wayland-session app|background|session your_app [with args]`.

Example snippet for sway config to launch terminal, app launcher and file manager scoped in `app-graphical.slice`:

    bindsym --to-code $mod+t exec exec wayland-session app foot
    bindsym --to-code $mod+r exec exec wayland-session app fuzzel --log-no-syslog
    bindsym --to-code $mod+e exec exec wayland-session app spacefm

When app launching is properly configured, WM service itself can be placed in `session.slice` by setting
environment variable `UWSM_USE_SESSION_SLICE=true` before generating units
(best to export this in `profile` before `wayland-session` invocation).

## Operation

### Short story:

Start variants:

- `wayland-session start ${wm}`: generates and starts templated units with `@${wm}` instance.
- `wayland-session start "${wm} with \"any complex\" arguments"`: also adds arguments for particular `@${wm}` instance.
- `wayland-session start select`: Invokes a menu to select WM from desktop entries for wayland-sessions. Selection is saved, previous selection is highlighted.
- `wayland-session start default`: Runs previously selected WM, if selection was made, otherwise invokes a menu.

When started, `wayland-session` will hold while wayland session is running, and terminate session if interrupted or killed.

Stop with `wayland-session stop`

To launch automatically after login on virtual console 1, add this to shell profile:

    if wayland-session may-start
    then
    	exec wayland-session start select
    fi

`may-start` checker command, among other things, **screens for being in interactive login shell which is essential**,
since profile sourcing can otherwise lead to nasty loops.

### Longer story, tour under the hood:

#### Start and bind

(At least for now) units are generated by the script (and plugins).

Run `wayland-session unitgen ${wm}` to populate `${XDG_RUNTIME_DIR}/systemd/user/` with them and do nothing else.
`wayland-session unitrm ${wm}` removes units.

WM argument may also contain the full literal command line for the WM with arguments, i.e.:

`wayland-session unitgen "${wm} with \"any complex\" arguments"`

In case of `select` or `default`, the command line is taken from selected desktop entry `Exec=` attribute.

Desktop entries can be overridden or added in `${XDG_DATA_HOME}/wayland-sessions/`.

Basic set of generated units:

- templated targets boud to stock systemd user-level targets
  - `wayland-session-pre@.target`
  - `wayland-session@.target`
  - `wayland-session-xdg-autostart@.target`
- templated services
  - `wayland-wm-env@.service` - environment preloader service
  - `wayland-wm@.service` - main WM service
- slices for apps nested in stock systemd user-level slices
  - `app-graphical.slice`
  - `background-graphical.slice`
  - `session-graphical.slice`
- tweaks
  - `wayland-wm@${wm}.service.d/args.conf`- if WM has arguments, they go here.
  - `app-@autostart.service.d/slice-tweak.conf` - assigns XDG autostart apps to `app-graphical.slice`
  - `xdg-desktop-portal-.service.d/slice-tweak.conf` - assigns XDG portal implementations to `app-graphical.slice`

After units are generated, WM can be started by: `systemctl --user start wayland-wm@${wm}.service`

Add `--wait` to hold terminal until session ends.

`exec` it from login shell to bind to login session:

`exec systemctl --user start --wait wayland-wm@${wm}.service`

Still if login session is terminated, wayland session will continue running, most likely no longer being accessible.

To also bind it the other way around, shell traps are used:

`trap "if systemctl --user is-active -q wayland-wm@${wm}.service ; then systemctl --user --stop wayland-wm@${wm}.service ; fi" INT EXIT HUP TERM`

This makes the end of login shell also be the end of wayland session.

#### Stop

Just stop the main service: `systemctl --user stop "wayland-wm@${wm}.service"`.

Wildcard `systemctl --user stop "wayland-wm@*.service"` will also work.

If start command was run with `exec` from login shell or via `.profile`,
this stop command also doubles as a logout command.

`wayland-session` is smart enough to find login session associated with current TTY
and export `$XDG_SESSION_ID`, `$XDG_VTNR` to user manager environment when invoked in `wayland-wm-env@${wm}.service`
(bound to `graphical-session-pre.target`). When `wayland-wm-env@${wm}.service` is stopped, those two variables will be cleaned up unconditionally.
(I really do not know it this mechanism is a good idea, but since there can be only one graphical session
per user with systemd, it seems like such).

#### Profile integration

This example does the same thing as `may-start` + `start` command combination described earlier: starts wayland session automatically upon login on tty1 if system is in `graphical.target`

**Screening for being in interactive login shell here is essential** (`[ "${0}" != "${0#-}" ]`). `wayland-wm-env@${wm}.service` sources profile,
which has a potential for nasty loops if run unconditionally. Other conditions are a recommendation:

    MY_WM=sway
    if [ "${0}" != "${0#-}" ] && \
       [ "$XDG_VTNR" = "1" ] && \
       systemctl is-active -q graphical.target && \
       ! systemctl --user is-active -q wayland-wm@*.service
    then
        wayland-session unitgen ${MY_WM}
        trap "if systemctl --user is-active -q wayland-wm@${MY_WM}.service ; then systemctl --user --stop wayland-wm@${MY_WM}.service ; fi" INT EXIT HUP TERM
        echo Starting ${MY_WM} WM
        systemctl --user start --wait wayland-wm@${MY_WM}.service &
        wait
        exit
    fi

## WM-specific actions

Plugins provide WM-specific functions.
See `#### Load WM plugin` comment section in `wayland-session` for function descriptions
and `wayland-session-plugins/*.sh.in` for examples.

## TODO

- since shell-start mode was dropped and the only mechanism that requires native shell is env loading contained to `wayland-wm-env@.service` invocations, maybe rewrite the whole thing in python

## Compliments

Inspired by and adapted some techniques from:

- [sway-services](https://github.com/xdbob/sway-services)
- [sway-systemd](https://github.com/alebastr/sway-systemd)
- [sway](https://github.com/swaywm/sway)
- [Presentation by Martin Pitt](https://people.debian.org/~mpitt/systemd.conf-2016-graphical-session.pdf)
