project(
  'uwsm',
  version: run_command('sh', 'version.sh', check: true).stdout().strip(),
  license: 'MIT',
  meson_version: '>=1.1'
)

PYTHON = import('python')
PYTHON_BIN_OPTION = get_option('python-bin')
PYTHON_PROG = PYTHON.find_installation(PYTHON_BIN_OPTION)
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
PKG_PYTHON_MODULES_DIR = join_paths(PKG_DATA_DIR, 'modules')
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

sh = find_program('sh', required: false)
waitpid = find_program('waitpid', required: false)
whiptail = find_program('whiptail', required: false)

PROJECT_NAME = meson.project_name()
PROJECT_VERSION = meson.project_version()

conf = configuration_data()
conf.set('PROJECT_NAME', PROJECT_NAME)
conf.set('PROJECT_VERSION', PROJECT_VERSION)
conf.set('PKG_DATA_DIR', PKG_DATA_DIR)
conf.set('PKG_PYTHON_MODULES_DIR', PKG_PYTHON_MODULES_DIR)
conf.set('PYTHON_BIN', PYTHON_BIN)
conf.set('SH_PATH', sh.found() ? sh.full_path() : '')
conf.set('WAITPID_PATH', waitpid.found() ? waitpid.full_path() : '')
conf.set('WHIPTAIL_PATH', whiptail.found() ? whiptail.full_path() : '')
if get_option('static-units').allowed()
  conf.set('STATIC_UNITS', true)
else
  conf.set('STATIC_UNITS', false)
endif
conf.set('BIN_NAME', 'uwsm')
conf.set(
  'BIN_PATH',
  join_paths(
    get_option('prefix'),
    get_option('bindir'),
    'uwsm',
  ),
)


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
subdir('example-units')
subdir('scripts')
subdir('man')
subdir('systemd/user')

uwsm_doc_files = [
  'README.md',
]

install_data(uwsm_doc_files, install_dir: PKG_DOC_DIR)

uwsm_license_files = [
  'LICENSE',
]

install_data(uwsm_license_files, install_dir: PKG_LICENSE_DIR)
