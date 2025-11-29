if get_option('fumon').allowed()
  configure_file(
    input: '80-fumon.preset.in',
    output: '80-fumon.preset',
    configuration: conf,
    install: true,
    install_dir: SD_USER_PRESET_DIR,
    install_mode: 'rw-r--r--',
  )
endif
if get_option('ttyautolock').allowed()
  configure_file(
    input: '80-ttyautolock.preset.in',
    output: '80-ttyautolock.preset',
    configuration: conf,
    install: true,
    install_dir: SD_USER_PRESET_DIR,
    install_mode: 'rw-r--r--',
  )
endif
