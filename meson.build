project(
  'uwsm',
  version: '0.14.4',
  license: 'MIT',
)

PYTHON = import('python')
PYTHON_PROG = PYTHON.find_installation('python3')
PYTHON_VERSION = PYTHON_PROG.language_version()
if not PYTHON_VERSION.version_compare('>=3.10')
  error('python version >=3.10 required, found: @0@'.format(PYTHON_VERSION))
endif

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

PROJECT_NAME = meson.project_name()
PROJECT_VERSION = meson.project_version()

PYTHON_BIN = get_option('python-bin')
if PYTHON_BIN == ''
  PYTHON_BIN = PYTHON_PROG.full_path()
endif

UWSM_BIN_NAME = get_option('uwsm-bin-name')
if UWSM_BIN_NAME == ''
  UWSM_BIN_NAME = meson.project_name()
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
GENERATED_DIR = meson.build_root() / 'uwsm-generated'

subdir('uwsm')
subdir('uwsm-plugins')
subdir('example-units')
subdir('scripts')

uwsm_doc_files = [
  'README.md',
]

install_data(uwsm_doc_files, install_dir: PKG_DOC_DIR)

uwsm_license_files = [
  'LICENSE',
]

install_data(uwsm_license_files, install_dir: PKG_LICENSE_DIR)
