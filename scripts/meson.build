want_uuctl = get_option('uuctl').allowed()
want_uwsm_app = get_option('uwsm-app').allowed()

configure_file(
  input: 'uuctl.sh',
  output: 'uuctl',
  copy: true,
  install: want_uuctl,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'uuctl.desktop',
  output: 'uuctl.desktop',
  copy: true,
  install: want_uuctl,
  install_dir: join_paths(get_option('datadir'), 'applications'),
  install_mode: 'rw-r--r--',
)

configure_file(
  input: 'uwsm-app.sh',
  output: 'uwsm-app',
  copy: true,
  install: want_uwsm_app,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)
