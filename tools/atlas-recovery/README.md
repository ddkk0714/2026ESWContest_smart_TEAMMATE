# ATLAS recovery

See [deployment and field checks](../../docs/reboot-recovery.md).

Run install.sh as root on the target board with role pi4 or pi5. No OS remount is performed. Only writable directories in systemd UnitPath are accepted. --runtime-only does not survive reboot.

Existing recovery.env and hub.env are preserved. New configuration is root-owned. Stop an existing manually launched broker before enabling its unit. Use lifecycle.sh status for diagnostics.

PC tests: python -m pytest tools/test_atlas_recovery.py -q
