UWSM(1)

# NAME

*UWSM* - Universal Wayland Session Manager.

# SYNOPSIS

*uwsm* [-h|-v] {_subcommand_} [_options_ ...]

# DESCRIPTION

Launches arbitrary wayland compositor via a set of systemd user units to
provide graphical user session with environment management, XDG autostart
support, clean shutdown. Provides helpers for launching applications as
scopes or services.

# SUBCOMMANDS

|[ *select*
:< Select default compositor Entry.
|  *start*
:  Start compositor and graphical session.
|  *finalize*
:  Send compositor-set variables and unit startup notification to systemd user
   manager.
|  *stop*
:  Stop graphical session and compositor.
|  *app*
:  Application unit launcher (with Desktop Entry support).
|  *check*
:  Perform state checks (for scripting and info).
|  *aux*
:  Technical functions for use inside units.

See corresponding _SUBCOMMANDS_ subsections below for further info.

Help for each subcommand is accessible by running "*uwsm* {_subcommand_} *-h*".

# CONFIGURATION

## Files

In XDG config hierarchy:
|[ *uwsm/env*
:<
|  *uwsm/env-*_${compositor}_
:  Environment (shell) to be sourced for the graphical session. Sourced from
   directories of increasing priority, in each directory common file is sourced
   first, then suffixed files in the order of items listed in
   *XDG_CURRENT_SESSION* var (lowercased).
|  *uwsm/default-id*
:  Stores Desktop Entry ID of default compositor.

Fallback is also extended into the system part of XDG data hierarchy, this can
be used for distro level defaults.

## Environment vars

|[ *UWSM_UNIT_RUNG*
:< (*run*|*home*)
| 
:  Which rung of systemd/user/ hierarchy to manage generated unit and drop-in
   files in: $XDG_RUNTIME_DIR or $XDG_CONFIG_HOME.
|  *UWSM_NO_TWEAKS*
:  (boolean value)
| 
:  Remove and do not generate tweak drop-ins for other software.
|  *UWSM_FINALIZE_VARNAMES*
:  (whitespace-separated names of env vars)
| 
:  Additional variables for "*uwsm finalize*".
|  *UWSM_WAIT_VARNAMES*
:  (whitespace-separated names of env vars)
| 
:  Variables to wait for in activation environment before proceeding to
   graphical session (in addition to *WAYLAND_DISPLAY*).
|  *UWSM_WAIT_VARNAMES_TIMEOUT*
:  (int value)
| 
:  Seconds to wait for variables to appear in activation environment.
   Essentially, startup timeout (default: 10).
|  *UWSM_WAIT_VARNAMES_SETTLETIME*
:  (float value)
| 
:  Seconds to pause after all expected vars found in activation environment
   (default: 0.2).
|  *UWSM_APP_UNIT_TYPE*
:  (*scope*|*service*)
| 
:  Default unit type for launching apps (default: *scope*).
|  *UWSM_SILENT_START*
:  (int or boolean value)
| 
:  _True_ or *1* to inhibit stdout messages from *uwsm start*. *2* to also
   inhibit warnings.
|  *DEBUG*
:  (int or boolean value)
| 
:  _True_ or positive number to dump debug info to stderr.

# OPERATION OVERVIEW

## Login Sequence Integration

*uwsm* can be launched by using conditional *exec* in shell profile to replace
login shell (see _Shell Profile Integration_ section).

Alternatively "*uwsm start* ..." command can be put into wayland session's
Desktop Entry to be launched by a display manager (see _Use Inside Desktop
Entry_ section).

## Compositor Selection

*uwsm* can run arbitrary compositor command line or a Desktop Entry by ID
(specifying Action ID is also supported).

Desktop Entry can also be selected via a *whiptail* menu (see _select_
subcommand section).

## Startup

See _start_ subcommand section for command syntax.

UWSM uses a set of units bound to standard user session targets:

- *wayland-session-pre@.target* (bound to *graphical-session-pre.target*)
	- *wayland-wm-env@.service* (environment preloader service)
- *wayland-session@.target* (bound to *graphical-session.target*)
	- *wayland-wm@.service* (service for the selected compositor)
- *wayland-session-xdg-autostart@.target* (bound to
  *xdg-desktop-autostart.target*)
- *wayland-session-shutdown.target* (conflicts with targets above for shutdown)
- *wayland-session-bindpid@.service* (PID-tracking session killswitch)
- *wayland-session-waitenv.service* (delays graphical session until vars appear)

Compositor ID (Desktop Entry ID or executable name) becomes the specifier
for all templated units.

At the stage of *graphical-session-pre.target*, the environment saved from
"*uwsm start*" context is loaded (or POSIX shell profile is sourced), uwsm
environment _files_ are sourced. The delta is exported to the systemd and D-Bus
activation environments by the environment preloader service and is marked for
cleanup at shutdown stage. Preloader shell context for convenience has
*IN_UWSM_ENV_PRELOADER* var set to *true*.

At the stage of *graphical-session.target* (before it) the main compositor unit
*wayland-wm@*_${ID}_*.service* and *wayland-session-waitenv.service* are
started.

Compositor should at least put *WAYLAND_DISPLAY* variable to systemd activation
environment. This will trigger uwsm's automatic finalization logic. Without
*WAYLAND_DISPLAY* in activation environment startup will timeout in 10 seconds.

Manual finalization is possible by running "*uwsm finalize*" (see _finalize_
subcommand section), also in combination with tweaking *UWSM_WAIT_VARNAMES*
and *UWSM_WAIT_VARNAMES_SETTLETIME* vars (see _Environment vars_ section).

Successful activation of compositor unit and existence of *WAYLAND_DISPLAY*
in activation environment will allow *graphical-session.target* to be declared
reached.

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
for you (see _app_ subcommand section).

The compositor is launched in *session.slice* by default (as recommended by
*systemd.special*(7)).

## Shutdown

Can be initiated by either:
- running *uwsm stop*
- stopping *wayland-wm@\*.service*
- starting *wayland-session-shutdown.target*

Systemd stops all user units in reverse, as it usually does. During deactivation
of *graphical-session-pre.target*, the environment preloader service cleans
activation environments by unsetting all variables that were marked for removal
during startup and finalization stages.

Do not use compositor's native exit mechanism or kill its process directly.

# SUBCOMMANDS

## select

Selects default wayland session compositor Desktop Entry.

	*uwsm* select

Invokes a whiptail menu to select default session among Desktop Entries in
*wayland-sessions* XDG data hierarchy. Writes to
*${XDG_CONFIG_HOME}/uwsm/default-id*. Nothing else is done. Returns *1* if
selection is cancelled. Can be used for scripting launch condition in shell
profile.

## check

Performs tests, returns *0* on success, *1* on failure.

*is-active*:

	*uwsm* check is-active [-h] [-v] [_compositor_]

|[     *-v*
:< show additional info
|      _compositor_
:  check for specific compositor


Checks if unit of specific *compositor* or *graphical-session\*.target* in
general is in active or activating state.

*may-start*:

	*uwsm* check may-start [-h] [-g [_S_]] [-v|-q] [_N_ ...]

|[     *-g* _S_
:< wait _S_ seconds for graphical.target in queue (default: 60; 0 or
   less disables check).
|      *-v*
:  show all failed tests
|      *-q*
:  be quiet
|      _N_ ...
:  allowed VT numbers (default: 1)


Checks whether it is OK to launch a wayland session via the following conditions:
- Running from login shell
- System is at *graphical.target*
- User *graphical-session\*.target* units are not yet active
- Foreground VT is among allowed (default: 1)

## start

Generates units for given compositor command line or Desktop Entry and starts
them.

	*uwsm* start [-h] [-D _name_[:_name_...]] [-a|-e] [-N _Name_] [-C _Comment_]
	\[-U {run|home}] [-t] [-o] [-n] -- _compositor_ [_args_ ...]

|[     *-F*
:< Hardcode mode, always write command line to unit drop-ins and use full paths.
|      *-D* _name_[:_name_...]
:  Names to fill *XDG_CURRENT_DESKTOP* with
   (:-separated). Existing var content is a starting point if no active
   session is running.
|      *-a*
:  Append desktop names set by -D to other sources (default).
|      *-e*
:  Use desktop names set by -D exclusively, discard other sources.
|      *-N* _Name_
:  Fancy name for compositor (filled from Desktop Entry by
   default).
|      *-C* _Comment_
:  Fancy description for compositor (filled from Desktop
   Entry by default).
|      *-U* {*run*|*home*}
:  Select rung for generated unit files: *run*: $XDG_RUNTIME_DIR/systemd/user
   (default), or *home*: $XDG_CONFIG_HOME/systemd/user. Permanent destination
   will save some time by removing need for reloading systemd. Managed files
   from other rung will be removed. Can be preset with *UWSM_UNIT_RUNG*
   environment var.
|      *-t*
:  Do not generate (and remove) tweak unit files. Can be preset with
   *UWSM_NO_TWEAKS* environment var.
|      *-g* _S_
:  Wait for S seconds for system graphical.target in queue and warn if timed out
   or not in queue (default: 60, negative to disable).
|      *-G* _S_
:  Wait for S seconds for system graphical.target in queue and abort if timed
   out or not in queue (overrides -g, default: -1, (disabled)).
|      *-o*
:  Only generate units, but do not start.
|      *-n*
:  Dry run, do not write or start anything.

The first argument of the compositor command line acts as an ID and should be
either one of:
- Executable name
- Desktop Entry ID (optionally with "*:*"-delimited action ID)
- Special value:
	- *select* - invoke menu to select compositor.
	- *default* - run previously selected compositor (or select if no
	  selection was saved).

If given as path, hardcode mode will be used implicitly.

Always use "*--*" to disambiguate dashed arguments intended for compositor
itself.

After units are (re)generated, *wayland-session-bindpid@*_${PID}_*.service* is
started, to track the PID of invoking *uwsm*, then *uwsm* process replaces
itself with *systemctl* execution that starts *wayland-wm@*_${ID}_*.service*
and waits for it to finish.

In order to complete the startup sequence, the compositor has to put
*WAYLAND_DISPLAY* into the systemd activation environment. This can be done
explicitly by making compositor run "*uwsm finalize*" command (see the next
subsection).

## finalize

For running by a compositor on startup.

	*uwsm* finalize [-h] [_VAR_NAME_ ...]

Exports *WAYLAND_DISPLAY*, *DISPLAY* and any defined vars mentioned by names
in arguments or in *UWSM_FINALIZE_VARNAMES* variable (whitespace-separated).
Then sends startup notification for the unit to systemd user manager.

*This is required* if compositor itself does not put *WAYLAND_DISPLAY* to
systemd activation environment, otherwise *wayland-session@.service* unit
or a dedicated *wayland-session-waitenv.service* unit will terminate due to
startup timeout.

*UWSM_FINALIZE_VARNAMES* variable can be prefilled by plugins.

Direct assignment as _VAR_NAME_=_value_ is also possible, but recommended only
for creating flags for *UWSM_WAIT_VARNAMES* mechanism.

## stop

Stops compositor and optionally removes generated units.

	*uwsm* stop [-h] [-r [_compositor_] [-U {run|home}] [-n]

|[     *-r* [_compositor_]
:< Also remove units (all or only _compositor_-specific).
|      *-U* {*run*|*home*}
:  Select rung for generated unit files: *run*: $XDG_RUNTIME_DIR/systemd/user
   (default), or *home*: $XDG_CONFIG_HOME/systemd/user. Permanent destination
   will save some time by removing need for reloading systemd. Managed files
   from other rung will be removed. Can be preset with *UWSM_UNIT_RUNG*
   environment var.
|      *-n*
:  Dry run, do not stop or remove anything.

## app

Application-to-unit launcher with Desktop Entry support.

	*uwsm* app [-h] [-s {*a*,*b*,*s*,_custom_.slice}] [-t {scope,service}]
	\[-a _app_name_] [-u _unit_name_] [-d _unit_description_]
	\[-S ] [-T] -- _application_ [_args_ ...]

|[     *-s* {*a*,*b*,*s*,_custom_.slice}
:< Slice selector (default: *a*):
| 
:     *a* - *app-graphical.slice*
| 
:     *b* - *background-graphical.slice*
| 
:     *s* - *session-graphical.slice*
| 
:     any slice by full name
|      *-t* {*scope*,*service*}
:  Type of unit to launch (default: *scope*, can be preset by
   *UWSM_APP_UNIT_TYPE* env var).
|      *-a* _app_name_
:  Override app name (a substring in unit name).
|      *-u* _unit_name_
:  Override the whole autogenerated unit name.
|      *-d* _unit_description_
:  Unit Description.
|      *-S* {*out*,*err*,*both*}
:  Silence stdout, stderr, or both.
|      *-T*
:  Launch app in a terminal. Allows command to be empty to just launch a
   terminal.


Application can be provided as a command with optional arguments, or a Desktop
Entry ID, optionally suffixed with "*:*"-delimited Action ID. If Desktop Entry
is being launched, arguments should be compatible with it.

Always use "*--*" to disambiguate dashed arguments intended for application
itself.

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
*${XDG_RUNTIME_DIR}/uwsm-app-daemon-out* pipe.

Arguments are expected to be *\\0*-delimited, leading *\\0* are stripped.
One command is received per write+close.

The first argument determines the behavior:

- *app*	the rest is processed the same as in "uwsm app"
- *ping*	just "pong" is returned\n
- *stop*	daemon is stopped\n


Single commands are prepended with *exec*, iterated commands are assembled with
trailing *&* each, followed by *wait*.

The purpose of all this is to skip all the expensive Python startup and import
routines that slow things down every time "*uwsm app*" is called. Instead the
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
		exec systemd-cat -t uwsm_start uwsm start default
	fi
```

This just starts a specific compositor depending on foreground VT:

```
	if uwsm may-start 1; then
		exec systemd-cat -t uwsm_start uwsm start sway.desktop
	elif uwsm may-start 2; then
		exec systemd-cat -t uwsm_start uwsm start labwc.desktop
	fi
```

Using "*uwsm check may-start*" as a condition is *essential*, not only to
prevent accidental startup attempts where they are not expected, but also since
startup may involve sourcing shell profile, which might lead to nasty loops.

See _check_ subcommand section for info on *may-start* checker.

*exec* allows uwsm to replace login shell in order to properly bind to user
session and handle session termination.

"*systemd-cat -t uwsm_start*" (optional) executes the command given to it
(*uwsm*) with its stdout and stderr connected to the systemd journal, tagged
with identifier "uwsm_start". See _systemd-cat(1)_ for more options.

# USE INSIDE DESKTOP ENTRY

To launch *uwsm* from a display/login manager, "*uwsm start*" can be used inside
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
- Command in *Exec=* should start with "*uwsm start*"
- It should not point to itself (as a combination of Desktop Entry ID and Action
  ID)
- It should not point to a Desktop Entry ID and Action ID that also uses `uwsm`

Potentially such entries may be found and used by *uwsm* itself, i.e. in shell
profile integration situation, or when launched manually. Following the
principles above ensures *uwsm* will properly recognize itself and parse
requested arguments inside the entry without any side effects.

# SEE ALSO

*uwsm-plugins*(3), *systemd-run*(1), *systemd-cat*(1), *systemd.special*(7)
