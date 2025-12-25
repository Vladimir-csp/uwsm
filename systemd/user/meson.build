install_data(
  'app-graphical.slice',
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
install_data(
  'background-graphical.slice',
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
install_data(
  'session-graphical.slice',
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
install_data(
  'wayland-session-envelope@.target',
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
install_data(
  'wayland-session-pre@.target',
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
install_data(
  'wayland-session-shutdown.target',
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
install_data(
  'wayland-session@.target',
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
install_data(
  'wayland-session-xdg-autostart@.target',
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
configure_file(
  input: 'wayland-session-bindpid@.service.in',
  output: 'wayland-session-bindpid@.service',
  configuration: conf,
  install: true,
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
configure_file(
  input: 'wayland-session-waitenv.service.in',
  output: 'wayland-session-waitenv.service',
  configuration: conf,
  install: true,
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
configure_file(
  input: 'wayland-wm-app-daemon.service.in',
  output: 'wayland-wm-app-daemon.service',
  configuration: conf,
  install: true,
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
configure_file(
  input: 'wayland-wm-env@.service.in',
  output: 'wayland-wm-env@.service',
  configuration: conf,
  install: true,
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)
configure_file(
  input: 'wayland-wm@.service.in',
  output: 'wayland-wm@.service',
  configuration: conf,
  install: true,
  install_dir: SD_USER_DIR,
  install_mode: 'rw-r--r--',
)

if get_option('fumon').allowed()
  install_data(
    'fumon.service',
    install_dir: SD_USER_DIR,
    install_mode: 'rw-r--r--',
  )
endif
if get_option('ttyautolock').allowed()
  install_data(
    'ttyautolock@.service',
    install_dir: SD_USER_DIR,
    install_mode: 'rw-r--r--',
  )
endif
