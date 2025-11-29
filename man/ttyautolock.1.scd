TTYAUTOLOCK(1)

# NAME

*ttyautolock* - Lock session if associated TTY loses focus

# SYNOPSIS

** [_SESSION_ID_ | *auto*]

# DESCRIPTION

Takes _session ID_ or *auto*, finds associated TTY,
watches */sys/class/tty/tty0/active* for changes. Locks session on transition
from associated TTY to any other. Locking is done via "*loginctl lock-session*".

*auto* argument relies on "*loginctl show-session auto*" behavior.

# SYSTEMD USER SERVICE

*ttyautolock@.service* with default *auto* specifier can be enabled and started
to always lock graphical session on TTY switch.

Alternatively it can be started on-demand and with custom session ID as
specifier.

# SEE ALSO

*loginct*(1), *inotifywait*(1)
