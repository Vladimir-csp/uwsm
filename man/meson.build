scdoc = dependency('scdoc', version: '>=1.9.2', native: true, required: get_option('man-pages'))
if scdoc.found()
  scdoc_prog = find_program(scdoc.get_variable(pkgconfig: 'scdoc'), native: true)
  mandir = get_option('mandir')
  man_files = [
    'uwsm.1.scd',
    'uwsm-plugins.3.scd',
  ]

  if get_option('fumon').allowed()
    man_files = man_files + ['fumon.1.scd']
  endif

  foreach filename : man_files
    topic = filename.split('.')[-3].split('/')[-1]
    section = filename.split('.')[-2]
    output = '@0@.@1@'.format(topic, section)

    custom_target(
      output,
      input: filename,
      output: output,
      command: scdoc_prog,
      install: true,
      feed: true,
      capture: true,
      install_dir: '@0@/man@1@'.format(mandir, section)
    )
  endforeach
endif
