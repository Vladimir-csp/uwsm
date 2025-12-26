project(
  'uwsm',
  version: run_command('sh', 'version.sh', check: true).stdout().strip(),
  license: 'MIT',
  meson_version: '>=1.3.0'
)

PYTHON = import('python')
PYTHON_BIN_OPTION = get_option('python-bin')
PYTHON_PROG = PYTHON.find_installation(PYTHON_BIN_OPTION, required: true)
PYTHON_VERSION = PYTHON_PROG.language_version()
if not PYTHON_VERSION.version_compare('>=3.10')
  error('python version >=3.10 required, found: @0@'.format(PYTHON_VERSION))
endif
PYTHON_BIN = PYTHON_PROG.full_path()

PKG_DATA_DIR = join_paths(
  get_option('prefix'),
  get_option('datadir'),
  meson.project_name(),
)
PKG_LIBEXEC_DIR = join_paths(
  get_option('prefix'),
  get_option('libexecdir'),
  meson.project_name(),
)
if get_option('public-modules').allowed()
  PKG_PYTHON_MODULES_DIR = PYTHON_PROG.get_install_dir()
  WRAPPER_INSERT_LINE = ''
else
  PKG_PYTHON_MODULES_DIR = join_paths(PKG_DATA_DIR, 'modules')
  WRAPPER_INSERT_LINE = '\n    sys.path.insert(1, "@0@")'.format(PKG_PYTHON_MODULES_DIR)
endif
PKG_DOC_DIR = get_option('docdir')
if PKG_DOC_DIR == ''
  PKG_DOC_DIR = join_paths(
    get_option('prefix'),
    get_option('datadir'),
    'doc',
    meson.project_name(),
  )
endif
PKG_LICENSE_DIR = get_option('licensedir')
if PKG_LICENSE_DIR == ''
  PKG_LICENSE_DIR = join_paths(
    get_option('prefix'),
    get_option('datadir'),
    'licenses',
    meson.project_name(),
  )
endif
SD_USER_DIR = join_paths(
  get_option('prefix'),
  'lib/systemd/user'
)
SD_USER_PRESET_DIR = join_paths(
  get_option('prefix'),
  'lib/systemd/user-preset'
)

SH_BIN = get_option('sh-bin')
WAITPID_BIN = get_option('waitpid-bin')
WHIPTAIL_BIN = get_option('whiptail-bin')
CBS = get_option('canonicalize-bins').allowed()

PROJECT_NAME = meson.project_name()
PROJECT_VERSION = meson.project_version()

conf = configuration_data()
conf.set('PROJECT_NAME', PROJECT_NAME)
conf.set('PROJECT_VERSION', PROJECT_VERSION)
conf.set('PKG_DATA_DIR', PKG_DATA_DIR)
conf.set('PKG_LIBEXEC_DIR', PKG_LIBEXEC_DIR)
conf.set('PKG_PYTHON_MODULES_DIR', PKG_PYTHON_MODULES_DIR)
conf.set('WRAPPER_INSERT_LINE', WRAPPER_INSERT_LINE)
conf.set('PYTHON_BIN', PYTHON_BIN)
conf.set(
  'SH_BIN',
  (CBS and not SH_BIN.startswith('/')) ? find_program(SH_BIN, required: true).full_path() : SH_BIN,
)
conf.set(
  'WAITPID_BIN',
  (CBS and not WAITPID_BIN.startswith('/') and find_program(WAITPID_BIN, required: false).found()) ? find_program(WAITPID_BIN, required: false).full_path() : WAITPID_BIN,
)
conf.set(
  'WHIPTAIL_BIN',
  (CBS and not WHIPTAIL_BIN.startswith('/') and find_program(WHIPTAIL_BIN, required: false).found()) ? find_program(WHIPTAIL_BIN, required: false).full_path() : WHIPTAIL_BIN,
)

conf.set('BIN_NAME', 'uwsm')
conf.set(
  'BIN_PATH',
  join_paths(
    get_option('prefix'),
    get_option('bindir'),
    'uwsm',
  ),
)
if get_option('fumon-preset').allowed()
  conf.set('FUMON_PRESET', 'enable')
else
  conf.set('FUMON_PRESET', 'disable')
endif
if get_option('ttyautolock-preset').allowed()
  conf.set('TTYAUTOLOCK_PRESET', 'enable')
else
  conf.set('TTYAUTOLOCK_PRESET', 'disable')
endif

COPY_TO_SUBDIR_SCRIPT = '''
import os
import sys
import shutil

args = dict()
args["build_root"] = sys.argv[1]
args["input"] = sys.argv[2]
args["output"] = sys.argv[3]

if not args["output"].startswith(args["build_root"]):
    sys.exit(2)

os.makedirs(os.path.dirname(args["output"]), exist_ok=True)

shutil.copy2(args["input"], args["output"])
'''
GENERATED_DIR = meson.project_build_root() / 'uwsm-generated'

subdir('uwsm')
subdir('uwsm-plugins')
subdir('uwsm-libexec')
subdir('example-units')
subdir('scripts')
subdir('man')
subdir('systemd/user')
subdir('systemd/user-preset')

uwsm_doc_files = [
  'README.md',
]

install_data(uwsm_doc_files, install_dir: PKG_DOC_DIR)

uwsm_license_files = [
  'LICENSE',
]

install_data(uwsm_license_files, install_dir: PKG_LICENSE_DIR)
