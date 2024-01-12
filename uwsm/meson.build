has_system_dbus = \
  run_command(
    PYTHON_PROG,
    '-c',
    'import dbus',
    check: false,
  ).returncode() == 0

has_system_xdg = \
  run_command(
    PYTHON_PROG,
    '-c',
    'import xdg',
    check: false,
  ).returncode() == 0

if not has_system_dbus
  error('python dbus module not found')
endif

if not has_system_xdg
  error('python xdg module not found')
endif

conf = configuration_data()
conf.set('PROJECT_NAME', PROJECT_NAME)
conf.set('PROJECT_VERSION', PROJECT_VERSION)
conf.set('PKG_DATA_DIR', PKG_DATA_DIR)
conf.set('PKG_PYTHON_MODULES_DIR', PKG_PYTHON_MODULES_DIR)
conf.set('PYTHON_BIN', PYTHON_BIN)
conf.set('BIN_NAME', UWSM_BIN_NAME)
conf.set(
  'BIN_PATH',
  join_paths(
    get_option('prefix'),
    get_option('bindir'),
    UWSM_BIN_NAME,
  ),
)

wrapper_file = configure_file(
  input: 'wrapper.py.in',
  output: UWSM_BIN_NAME,
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

init_file = configure_file(
  output: '__init__.py',
  capture: true,
  command: [
    PYTHON_PROG,
    '-c',
    'import sys; sys.exit(0)',
  ],
)

params_file = configure_file(
  input: 'params.py.in',
  output: 'params.py',
  configuration: conf,
)

# Here we copy params.py to a separate folder, along with an
# __init__.py file, so that language servers can find the module.
# The language server also needs to be told to search for modules
# in `GENERATED_DIR`. With VSCode's Python extension, that's the
# `python.analysis.extraPaths` setting.
generated_uwsm_dir = GENERATED_DIR / 'uwsm'

run_command(
  PYTHON_PROG,
  '-c',
  COPY_TO_SUBDIR_SCRIPT,
  meson.build_root(),
  params_file,
  generated_uwsm_dir / 'params.py',
  check: true,
)

run_command(
  PYTHON_PROG,
  '-c',
  COPY_TO_SUBDIR_SCRIPT,
  meson.build_root(),
  init_file,
  generated_uwsm_dir / '__init__.py',
  check: true,
)

uwsm_sources = [
  '__init__.py',
  'main.py',
  params_file,
]

install_data(
  uwsm_sources,
  install_dir: PKG_PYTHON_MODULES_DIR / 'uwsm',
)
