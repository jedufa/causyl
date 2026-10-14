#!/bin/bash

if [ "$1" == "on" ]; then
  sudo ufw allow 80
  echo "Public access ENABLED"
elif [ "$1" == "off" ]; then
  sudo ufw deny 80
  echo "Public access DISABLED"
else
  echo "Usage: tools/bin/toggle_public.sh [on|off]"
fi
