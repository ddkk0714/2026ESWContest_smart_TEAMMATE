#!/bin/sh
# ToF 자세 서비스를 내린다.
for p in $(pidof deskmate_tofposture); do kill "$p"; echo "stopped $p"; done
