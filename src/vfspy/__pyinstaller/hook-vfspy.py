"""Bundle the native bridge at the path used by vfspy.process."""

from PyInstaller.utils.hooks import collect_data_files

# Collect executable resources as binaries so dependencies and executable
# permissions are handled by PyInstaller, including in onefile builds.
binaries = collect_data_files("vfspy", includes=["_bin/vfspy-native", "_bin/vfspy-native.exe"])
