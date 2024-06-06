UWSM(1)

# NAME

*UWSM* - Universal Wayland Session Manager.

# SYNOPSIS

*uwsm [-h] {subcommand} [options...]*

# DESCRIPTION

Launches arbitrary wayland compositor via a set of systemd user units to
provide graphical user session with environment management, XDG autostart
support, clean shutdown. Provides helpers for launching applications as
scopes or services.

# SUBCOMMANDS

|[ *select*
:< Select default compositor Entry.
|  *start*
:  Start compositor.
|  *finalize*
:  Send compositor-set variables and unit startup notification to systemd user
   manager.
|  *stop*
:  Stop compositor.
|  *app*
:  Application unit launcher (with Desktop Entry support).
|  *check*
:  Perform state checks (for scripting and info).
|  *aux*
:  Technical functions for use inside units.

See corresponding _SUBCOMMANDS_ subsections below for further info.

Help for each subcommand is accessible by running *uwsm {subcommand} -h*.

# CONFIGURATION

## Files

In XDG config hierarchy:
|[ *uwsm-env*
:<
|  *uwsm-env-${compositor}*
:  Environment (shell) to be sourced for
   the graphical session. Sourced from directories of increasing priority,
   in each directory common file is sourced first, then suffixed files in the
   order of items listed in *XDG_CURRENT_SESSION* var (lowercased).
|  *uwsm-default-id*
:  Stores Desktop Entry ID of default compositor.

## Environment vars

|[ *UWSM_USE_SESSION_SLICE*
:< (true|false)
| 
:  Put compositor unit in *session.slice*
|  *UWSM_APP_UNIT_TYPE*
:  (service|scope)
| 
:  Default unit type for launching apps
|  DEBUG
:  Dump debug info to stderr

# OPERATION OVERVIEW

## Login Sequence Integration

*uwsm* can be launched by using conditional _exec_ in shell profile to replace
login shell (see _Shell Profile Integration_ section).

Alternatively *uwsm start ...* command can be put into wayland session's
Desktop Entry to be launched by a display manager (see _Use Inside Desktop
Entry_ section).

## Compositor Selection

*uwsm* can run arbitrary compositor command line or a Desktop Entry by ID
(specifying Action ID is also supported).

Desktop Entry can also be selected via a _whiptail_ menu (see _select_
subcommand section).

## Startup

See _start_ subcommand section for command syntax.

A set of runtime units bound to standard user session targets is generated:
- *wayland-session-pre@.target* (bound to *graphical-session-pre.target*)
	- *wayland-wm-env@.service* (environment preloader service)
- *wayland-session@.target* (bound to *graphical-session.target*)
	- *wayland-wm@.service* (service for the selected compositor)
- *wayland-session-xdg-autostart@.target* (bound to
  *xdg-desktop-autostart.target*)

Compositor ID (Desktop Entry ID or executable name) becomes the specifier
for all templated units.

At the stage of *graphical-session-pre.target* environment is
sourced from shell profile and from uwsm environment _files_. The delta is
exported to systemd and dbus activation environments by environment preloader
service and is marked for cleanup at shutdown stage.

At the stage of *graphical-session.target* the main compositor unit is started.

Compositor should finalize its startup by running *uwsm finalize* (see
_finalize_ subcommand section). This adds *WAYLAND_DISPLAY* var set by
compositor to activation environments and puts compositor's unit in active
state, so *graphical-session.target* is declared reached, allowing dependent
units to proceed with startup.

Finally, *xdg-desktop-autostart.target* is activated.

## Inside session

It is highly recommended to configure the compositor or app launcher to launch
apps as scopes or services in special user session slices (*app.slice*,
*background.slice*, *session.slice*). *uwsm* provides custom nested slices for
apps to live in and be terminated on session end:
- *app-graphical.slice*
- *background-graphical.slice*
- *session-graphical.slice*

A helper *app* subcommand is provided to handle all the systemd-run invocations
for you (See _app_ subcommand section).

If app launching is configured as recommended, *uwsm* can be set to launch
compositor in *session.slice* (as recommended by _man systemd.special_) by
adding "-S" to *start* subcommand, or adding this to the environment in which
*uwsm start* is launched:

	UWSM_USE_SESSION_SLICE=true

## Shutdown

Systemd stops all user units in reverse, as it usually does. During shutdown
of *graphical-session-pre.target* environment preloader service removes all
environment vars marked for removal during startup and finalization from
activation environments.

# SUBCOMMANDS

## select

Selects default wayland session compositor Desktop Entry.

	*uwsm select*

Invokes a whiptail menu to select default session among Desktop Entries in
*wayland-sessions* XDG data hierarchy. Writes to
*${XDG_CONFIG_HOME}/uwsm-default-id*. Nothing else is done. Returns *1* if
selection is cancelled. Can be used for scripting launch condition in shell
profile.

## check

Performs tests, returns *0* on success, *1* on failure.

*is-active*:

	*uwsm check is-active [-h] [-v] [compositor]*

|[     *-v*
:< show additional info
|      *compositor*
:  check for specific compositor


Checks if unit of specific *compositor* or *graphical-session\*.target* in
general is in active or activating state.

*may-start*:

	*uwsm check may-start [-h] [-v|-q] [N ...]*

|[     *-v*
:< show all failed tests
|      *-q*
:  be quiet
|      *N* ...
:  allowed VT numbers (default: 1)


Checks whether it is OK to launch a wayland session by following conditions:
- Running from login shell
- System is at *graphical.target*
- User *graphical-session\*.target* units are not yet active
- Foreground VT is among allowed (default: 1)

## start

Generates units for given compositor command line or Desktop Entry and starts
them.

	*uwsm start [-h] [-D name[:name...]] [-a|-e] [-N Name] [-C Comment]
	\[-S|-A] [-o] [-n] -- compositor [args ...]*

|[     *-D name[:name...]*
:< Names to fill *XDG_CURRENT_DESKTOP* with
   (:-separated). Existing var content is a starting point if no active
   session is running.
|      *-a*
:  Append desktop names set by -D to other sources (default).
|      *-e*
:  Use desktop names set by -D exclusively, discard other sources.
|      *-N Name*
:  Fancy name for compositor (filled from Desktop Entry by
   default).
|      *-C Comment*
:  Fancy description for compositor (filled from Desktop
   Entry by default).
|      *-S*
:  Launch compositor in session.slice.
|      *-A*
:  Launch compositor in app.slice.
|      *-o*
:  Only generate units, but do not start.
|      *-n*
:  Do not write or start anything.

The first argument from the compositor command line acts as an ID and should be
either one of:
- Executable name
- Desktop Entry ID (optionally with "*:*"-delimited action ID)
- Special value:
	- *select* - invoke menu to select compositor.
	- *default* - run previously selected compositor (or select if no
	  selection was saved).

Always use *--* to disambiguate dashed arguments intended for compositor itself.

After units are (re)generated, *wayland-wm@${ID}.service* is started.
*uwsm start* will hold until the unit is stopped, or stop the unit if itself
interrupted or terminated.

In order to complete the startup sequence, compositor has to run *uwsm finalize*
(see the next subsection).

## finalize

For running by compositor on startup.

	*uwsm finalize [-h] [VAR_NAME ...]*

Exports *WAYLAND_DISPLAY*, *DISPLAY* and any defined vars mentioned by names
in arguments. Sends startup notification for the unit to systemd user manager.

*This is required* to be run by compositor, otherwise *wayland-session@.service*
unit will terminate due to startup timeout.

## stop

Stops compositor and optionally removes generated units.

	*uwsm stop [-h] [-r [wm,wm.desktop[:action]]] [-n]*

|[ *-r [wm,wm.desktop[:action]*
:< Also remove units (all or only compositor-specific).
|  *-n*
:  Dry run, do not stop or remove anything.

## app

Application-to-unit launcher with Desktop Entry support.

	*uwsm app [-h] [-s {a,b,s,custom.slice}] [-t {scope,service}]
	\[-a app_name] [-u unit_name] [-d unit_description] [-T] -- application
	\[args ...]*

|[ *-s {a,b,s,custom.slice}*
:< Slice selector (default: a):
| 
:     *a*pp-graphical.slice
| 
:     *b*ackground-graphical.slice
| 
:     *s*ession-graphical.slice
| 
:     any slice by full name
|  *-t {scope,service}*
:  Type of unit to launch (default: service, can be preset by UWSM_APP_UNIT_TYPE
   env var).
|  *-a app_name*
:  Override app name (a substring in unit name).
|  *-u unit_name*
:  Override the whole autogenerated unit name.
|  *-d unit_description*
:  Unit Description.
|  *-T*
:  Launch app in a terminal. Allows command to be empty to just launch a
   terminal.


Application can be provided as a command with optional arguments, or a Desktop
Entry ID, optionally suffixed with "*:*"-delimited Action ID. If Destop Entry
is being launched, arguments should be compatible with it.

Always use *--* to disambiguate dashed arguments intended for application itself.

## aux

For use in systemd user services. Can only be called by systemd user manager.

|[ *prepare-env*
:< Prepares environment (for use in ExecStart in *wayland-wm-env@.service*
   bound to *wayland-session-pre@.target*).
|  *cleanup-env*
:  Cleans up environment (for use ExecStop in in *wayland-wm-env@.service*
   bound to *wayland-session-pre@.target*).
|  *exec*
:  Executes a command with arguments or a desktop entry (for use in Exec in
   *wayland-wm@.service* bound to *wayland-session@.target*).
|  *app-daemon*
:  Daemon for faster app argument generation, used by *uwsm-app* client.

# APP DAEMON

Provided as *wayland-wm-app-daemon.service* to be started on-demand.

Daemon receives app arguments from *${XDG_RUNTIME_DIR}/uwsm-app-daemon-in*
pipe.
Resulting arguments are formatted as shell code and written to
*${{XDG_RUNTIME_DIR}/uwsm-app-daemon-out* pipe.

Arguments are expected to be *\\0*-delimited, leading *\\0* are stripped.
One command is received per write+close.

The first argument determines the behavior:

- *app*	the rest is processed the same as in "{BIN_NAME} app"\n
- *ping*	just "pong" is returned\n
- *stop*	daemon is stopped\n


Single commands are prepended with *exec*, iterated commands are assembled with
trailing *&* each, followed by *wait*.

The purpose of all this is to skip all the expensive python startup and import
routines that slow things down every time *uwsm app* is called. Instead the
daemon does it once and then listens for requests, while a simple shell script
may dump arguments to one pipe and run the code received from another via eval,
which is much faster.

The simplest script is:

```
	#!/bin/sh
	printf '\0%s' app "$@" > "${XDG_RUNTIME_DIR}/uwsm-app-daemon-in"
	IFS='' read -r cmd < "${XDG_RUNTIME_DIR}/uwsm-app-daemon-out"
	eval "$cmd"
```

Provided *uwsm-app* client script is a bit smarter: it can start the daemon,
applies timeouts, and supports newlines in returned args.

# SHELL PROFILE INTEGRATION

To launch *uwsm* automatically on login, add one of constructs below (or
similar) to shell profile.

This asks to select a compositor (or refuse and continue with login shell)
when logged in on VT 1:

```
	if uwsm may-start && uwsm select; then
		exec uwsm start default
	fi
```

This just starts a specific compositor depending on foreground VT:

```
	if uwsm may-start 1; then
		exec uwsm start sway.desktop
	elif uwsm may-start 2; then
		exec uwsm start labwc.desktop
	fi
```

Using *uwsm may-start* as a condition is *essential*, not only to prevent
accidental startup attempts where they are not expected, but also since startup
involves sourcing shell profile, which might lead to nasty loops.

*exec* allows uwsm to replace login shell in order to properly bind to user
session and handle session termination.

See _check_ subcommand section for info on *check may-start*.

# USE INSIDE DESKTOP ENTRY

To launch *uwsm* from a display/login manager, *uwsm start* can be used inside
Desktop Entries. Example
*/usr/local/share/wayland-sessions/my-compositor.desktop*:

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
- Command in *Exec=* should start with *uwsm start*
- It should not launch a desktop entry, only an executable.

Potentially such entries may be found and used by *uwsm* itself, i.e. in shell
profile integration situation, or when launched manually. Following the
principles above ensures *uwsm* will properly recognize itself and parse
requested arguments inside the entry without any side effects.
