#!/bin/sh
# Run on the Pi after uploading the UI IPK. Package removal recreates appdata.
# Preserve only this app's two known configuration files, with root-only backup.
set -eu
app_id=com.atlas.app.deskmate_display
app_home=/data/profiles/0/appdata/com.atlas.app.deskmate_display/home
config_dir=$app_home/.config/deskmate
ipk=/tmp/download/deskmate-workspace.ipk
test -f "$ipk"
backup_dir=$(mktemp -d /tmp/deskmate-ui-prefs.XXXXXX)
chmod 700 "$backup_dir"
abusctl call com.atlas.AppManager1 Stop "$app_id"
for name in workspace.json settings.json; do
  if test -f "$config_dir/$name"; then
    cp "$config_dir/$name" "$backup_dir/$name"
    chmod 600 "$backup_dir/$name"
  fi
done
abusctl call com.atlas.PackageManager1 Remove "$app_id"
abusctl call com.atlas.PackageManager1 Install "$app_id" "$ipk"
# First launch creates appdata with the newly allocated package user.
abusctl call com.atlas.AppManager1 Start "$app_id"
if test -f "$backup_dir/workspace.json" || test -f "$backup_dir/settings.json"; then
  attempt=0
  until abusctl call com.atlas.AppManager1 GetStatus "$app_id" | grep -q '"Running"'; do
    attempt=$((attempt + 1))
    if test "$attempt" -ge 40; then
      printf 'App did not start; settings backup retained at %s\n' "$backup_dir" >&2
      exit 1
    fi
    sleep 0.25
  done
  abusctl call com.atlas.AppManager1 Stop "$app_id"
  owner=$(stat -c '%u:%g' "$app_home")
  mkdir -p "$config_dir"
  chown "$owner" "$app_home/.config" "$config_dir"
  chmod 2770 "$app_home/.config" "$config_dir"
  for name in workspace.json settings.json; do
    if test -f "$backup_dir/$name"; then
      cp "$backup_dir/$name" "$config_dir/$name"
      chown "$owner" "$config_dir/$name"
      chmod 660 "$config_dir/$name"
    fi
  done
  abusctl call com.atlas.AppManager1 Start "$app_id"
fi
printf 'UI settings backup: %s\n' "$backup_dir"
