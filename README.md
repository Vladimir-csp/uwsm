# Universal Wayland Session Manager

This is an experiment in creating a versatile tool for launching
any standalone Wayland WM with environment management and systemd integration.

WIP. Use at your onw risk.

## Concepts and features

- WM-specific behavior is handled by plugins
    - currently supported: sway, wayfire
- Systemd units are treated with hierarchy and universality in mind:
    - named from common to specific: `wayland-${category}@${wm}.${unit_type}`
    - WM-specific part is abstracted to `@${wm}` part
    - allow for high-level `name-.d` drop-ins
- Idempotently (well, best-effort-idempotently) handle environment:
    - On startup environment is prepared and exported into systemd user manager
    - Special variables are imported back from systemd user manager at various stages of startup
    - On shutdown variables that were exported are unset from systemd user manager
    - Lists of variables for export import-back and cleanup are determined algorithmically by:
        - comparing environment before and after preparation procedures
        - boolean operations with predefined lists (tweakable by plugins)
- Two (and a half) modes of operation:
    - Starting a service
    - Running from shell (with two choices of scope)
- Written in POSIX shell (a smidgen of masochism went into this code)

## Full systemd service operation

(At least for now) Units are provided by built-in runtime generator.

Run `wayland-session ${wm} unitgen` to populate `${XDG_RUNTIME_DIR}` with them.

After that: `systemctl --user start --wait wayland-wm@${wm}.service` to start WM.

Then to stop: `systemctl --user stop "wayland-wm@*.service"` (no need to specify WM here).
If start command was run with `exec`, (i.e. from login shell on a tty or via `.profile`), this stop command is also a logout command.

`wayland-session` is smart enough to find login session associated with current TTY and add (and cleanup) `$XDG_SESSION_ID`, `$XDG_VTNR` to user manager environment. (I really do not know it this is a good idea, but since there can be only one graphical session per user with systemd, seems like such) 

Pros:

- Everything happens automagcally, maximum usage of systemd features

Cons:

- Pro#1 can be a con depending on your philosophy. I honestly understand both sides of this.
- (Probably counts as such) WM and its descendants are not a part of login session. In recommended way of starting graphical session is to exec `systemctl --user start --wait ...` then this command will be the sole occupant of login session apart from `/bin/login`.

## Partial systemd operation

In this mode WM is launched directly from the script, and the script manages startup, targets, and eventual cleanup.

`wayland-session $wm start` to start WM, In this mode WM is put into diretly descendant scope with logging to jouranl (unit: `wayland-wm-${WM}.scope`, log identifier: `wayland-wm-${WM}`

`wayland-session $wm intstart` also reexec the script itself with logging to journald (log identifier: `wayland-session-${WM}`)

Pros:

- (probably counts as such) WM and its descendants are a direct part of login session.

Cons:

- This mode is kinda semi-abandoned ATM.

## WM-specific actions

Plugins provide WM support and associated functions. See `wayland-session-plugins/*.sh.in` for examples.

## Compliments

Inspired by and adapted some techniques from:

- [sway-services](https://github.com/xdbob/sway-services)
- [sway-systemd](https://github.com/alebastr/sway-systemd)
- [sway](https://github.com/swaywm/sway)
- [Presentation by Martin Pitt](https://people.debian.org/~mpitt/systemd.conf-2016-graphical-session.pdf)