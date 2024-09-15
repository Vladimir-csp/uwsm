UWSM-PLUGINS(3)

# NAME

*UWSM-plugins* - Plugins for Universal Wayland Session Manager.

# DESCRIPTION

Shell plugins provide compositor-specific functions during environment
preparation.

Located in _${PREFIX}_*/share/uwsm/plugins/* and named
_${\_\_WM\_BIN\_ID\_\_}_*.sh*, they should only contain specifically named
functions.

*${\_\_WM\_BIN\_ID\_\_}* is derived from the item 0 of compositor command line
by applying *s/(^[^a-zA-Z]|[^a-zA-Z0-9\_])+/\_/* and converting to lower case.

It is used as plugin id and suffix in function names.

## Variables available to plugins

*\_\_WM\_ID\_\_*
	compositor ID, effective first argument of *start*.
*\_\_WM\_ID\_UNIT\_STRING\_\_*
	compositor ID escaped for systemd unit name.
*\_\_WM\_BIN\_ID\_\_*
	processed first item of compositor argv.
*\_\_WM\_DESKTOP\_NAMES\_\_*
	*:*-separated desktop names from *DesktopNames=* of entry and
	*-D* cli argument.
*\_\_WM\_FIRST\_DESKTOP\_NAME\_\_*
	first of the above.
*\_\_WM\_DESKTOP\_NAMES\_LOWERCASE\_\_*
	same as *\_\_WM\_DESKTOP\_NAMES\_\_*, but in lower case.
*\_\_WM\_FIRST\_DESKTOP\_NAME\_LOWERCASE\_\_*
	first of the above.
*\_\_WM\_DESKTOP\_NAMES\_EXCLUSIVE\_\_*
	(*true*|*false*) indicates that
	*\_\_WM\_DESKTOP\_NAMES\_\_* came from cli argument and are
	marked as exclusive.
*\_\_OIFS\_\_*
	contains shell default field separator (_space_, _tab_, _newline_)
	for convenient restoring.

## Standard functions

*load\_wm\_env*
	standard function for loading env files
*process\_config\_dirs*
	iterates over XDG Config hierarchy and system part of XDG Data hierarchy
	(decreasing priority)
*process\_config\_dirs\_reversed*
	(called by *load\_wm\_env*), same as *process\_config\_dirs* but in
	reverse (increasing priority)
*in\_each\_config\_dir\_reversed*
	called by *process\_config\_dirs\_reversed* for each config dir
	as *$1*, loads *uwsm/env*, *uwsm/env-*_${desktop}_ files
*in\_each\_config\_dir*
	called by *process\_config\_dirs* for each config dir as *$1*,
	does nothing ATM
*source\_file*
	sources *$1* file if exists, providing messages for log.

See code inside *uwsm/main.py* for more auxillary funcions.

## Functions that can be added by plugins

*quirks\_\_*$_{\_\_WM\_BIN\_ID\_\_}_++
	called before env loading.

These functions will be called instead of standard funcions if defined:

*load\_wm\_env\_\_*$_{\_\_WM\_BIN\_ID\_\_}_++
*process\_config\_dirs\_reversed\_\_*$_{\_\_WM\_BIN\_ID\_\_}_++
*in\_each\_config\_dir\_reversed\_\_*$_{\_\_WM\_BIN\_ID\_\_}_++
*process\_config\_dirs\_\_*$_{\_\_WM\_BIN\_ID\_\_}_++
*in\_each\_config\_dir\_\_*$_{\_\_WM\_BIN\_ID\_\_}_

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
  # or set a var to modify what sourcing uwsm/env, uwsm/env-${__WM_ID__}
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
```
