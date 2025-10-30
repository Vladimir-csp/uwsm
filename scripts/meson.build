if get_option('uuctl').allowed()
  install_data(
    'uuctl.sh',
    rename: 'uuctl',
    install_dir: get_option('bindir'),
    install_mode: 'rwxr-xr-x',
  )
  install_data(
    'uuctl.desktop',
    install_dir: join_paths(get_option('datadir'), 'applications'),
    install_mode: 'rw-r--r--',
  )
endif

if get_option('fumon').allowed()
  install_data(
    'fumon.sh',
    rename: 'fumon',
    install_dir: get_option('bindir'),
    install_mode: 'rwxr-xr-x',
  )
endif

if get_option('wait-tray').allowed()
  install_data(
    'wait-tray.sh',
    rename: 'wait-tray',
    install_dir: get_option('bindir'),
    install_mode: 'rwxr-xr-x',
  )
endif

if get_option('uwsm-app').allowed()
  install_data(
    'uwsm-app.sh',
    rename: 'uwsm-app',
    install_dir: get_option('bindir'),
    install_mode: 'rwxr-xr-x',
  )
  install_symlink(
    'uwsm-terminal',
    install_dir: get_option('bindir'),
    pointing_to: 'uwsm-app',
  )
  install_symlink(
    'uwsm-terminal-service',
    install_dir: get_option('bindir'),
    pointing_to: 'uwsm-app',
  )
  install_symlink(
    'uwsm-terminal-scope',
    install_dir: get_option('bindir'),
    pointing_to: 'uwsm-app',
  )
endif
