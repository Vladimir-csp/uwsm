FUMON(1)

# NAME

*FUMon* - Failed Units Monitor.

# SYNOPSIS

*fumon*

# DESCRIPTION

Watches for user units entering failed state, sends notifications via
*notify-send*.

Service is managed by user unit *fumon.service*, wanted by
*graphical-session.target*. Recommendation for packagers is to not enable this
unit during installation.

If it is indeed not enabled by default, enable and start it with:

	*systemctl --user enable --now fumon.service*

# SEE ALSO

*busctl*(1), *notify-send*(1), *uwsm*(1)
