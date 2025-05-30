UUCTL(1)

# NAME

*uuctl* - Menu for controlling systemd user units.

# SYNOPSIS

*uuctl* [-ah] [_menu_] [_menu args_ ...]

# DESCRIPTION

Utilizing one of dmenu-like apps, presents a menu of user units, then actions
to be performed on the selected one.

Suported menu apps:

- walker
- fuzzel
- wofi
- rofi
- tofi
- bemenu
- wmenu
- dmenu

Menu app is autoselected, but can be specified manually as a single argument.

If more arguments are given, they are used as full menu command line, so
a custom menu command not limited to the predefined list may be given.
The last argument is expected to be a prompt option (-p or analogous).

# OPTIONS

|[ *-a*|*--all*
:< Show all units without filtering out uwsm's compositor services.
|  *-h*|*--help*
:  Show help and exit

# SEE ALSO

*uwsm*(1), *systemctl*(1)
