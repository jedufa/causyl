#!/bin/bash
# WSL-friendly: load system + user bashrc (colors, prompt), then activate venv.

exec /bin/bash --rcfile <(/bin/cat <<'EOF'
# Force interactive behavior for rc loading
set -o emacs

# Seed a sane baseline PATH in case the caller environment is missing core bins.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"

# Load system-wide bashrc (Ubuntu prompt/colors live here)
if [ -r /etc/bash.bashrc ]; then
  source /etc/bash.bashrc
fi

# Load user bashrc (aliases, git prompt, etc.)
if [ -r ~/.bashrc ]; then
  # Some .bashrc files return early unless interactive; we ARE interactive here, but be explicit
  source ~/.bashrc
fi

# Activate venv
source "$HOME/venvs/emergence/bin/activate"
EOF
) -i
