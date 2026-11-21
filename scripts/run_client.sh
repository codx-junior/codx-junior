#!/bin/bash
# Dynamically set CODX_JUNIOR_PATH to the parent directory of the script
export CODX_JUNIOR_PATH="$(cd "$(dirname "$0")/.." && pwd)"
source ${CODX_JUNIOR_PATH}/set_env.sh

echo "CODX_JUNIOR_PATH: $CODX_JUNIOR_PATH"
echo "Starting client USER: ${USER} HOME: ${HOME}"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

# v25 requires extra dependencies on Debian/Ubuntu
nvm install v24.12.0

cd ${CODX_JUNIOR_PATH}/client

# `DEBUG=0` is a value, not "unset": the old test ([ "$DEBUG" != "" ]) treated
# every non-empty value as debug, so a container configured for production ran
# `npm install` and the Vite dev server on every restart. That is what blocks a
# restart: the install needs the network and the UI stays down until it ends.
case "${DEBUG:-0}" in
  1|true|TRUE|yes|on)
    echo "*********** codx-junior client DEBUG ************"
    # Dependencies belong to the image/build, not to every start. Install only
    # for a fresh checkout that has no node_modules yet.
    [ -d node_modules ] || npm install
    npm run dev
    ;;
  *)
    echo "*********** codx-junior client PRODUCTION ************"
    if [ ! -f dist/index.html ]; then
      echo "ERROR: client/dist is missing; build it once with: npm run build-only" >&2
      exit 1
    fi
    npm run preview
    ;;
esac
