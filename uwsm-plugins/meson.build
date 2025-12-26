uwsm_plugins = [
  'labwc.sh',
  'wayfire.sh',
  'sway.sh',
  'hyprland.sh',
]

install_data(
  uwsm_plugins,
  install_dir: PKG_DATA_DIR / 'plugins',
)

install_symlink(
  'start_hyprland.sh',
  install_dir: PKG_DATA_DIR / 'plugins',
  pointing_to: 'hyprland.sh',
)
