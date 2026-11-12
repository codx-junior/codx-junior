#!/bin/bash

# Dynamically set CODX_JUNIOR_PATH to the parent directory of the script
export CODX_JUNIOR_PATH="$(cd "$(dirname "$0")/.." && pwd)"
echo "CODX_JUNIOR_PATH: $CODX_JUNIOR_PATH"
echo "Installing api. USER: ${USER} ($(id -u):$(id -g)) HOME: ${HOME}"
echo "Fix permissions"
sudo chown -R ${USER} ${HOME}

source ${CODX_JUNIOR_PATH}/set_env.sh

# Install docker
codx docker &

export PYTHONPATH=${CODX_JUNIOR_PATH}/api

cd ${CODX_JUNIOR_PATH}/api

if [ ! -d "$CODX_JUNIOR_API_VENV/bin" ]; then
  echo "Installing codx-junior API for the first time at $CODX_JUNIOR_API_VENV ...will take some time."

  ls -la /home/codx-junior

  echo "Ensure folder exists"
  mkdir -p ${CODX_JUNIOR_API_VENV}

  ls -la /home/codx-junior

  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Add uv to PATH
  PATH=$HOME/.local/bin:$PATH
  # No hardlinks in docker
  export UV_LINK_MODE=copy
  
  # Create environment using uv with specified Python version
  uv venv --python 3.11 "$CODX_JUNIOR_API_VENV"
  source "${CODX_JUNIOR_API_VENV}/bin/activate"

  # Extras installed by default: local embeddings (torch/sentence-transformers)
  # and the browser client. A slim deployment can opt out with
  # CODX_JUNIOR_API_EXTRAS="" (see codx-junior-installer/demo/Dockerfile).
  API_EXTRAS=${CODX_JUNIOR_API_EXTRAS:-local-models,browser,milvus}

  # Install editable package using uv with the best-match strategy
  if [ -n "$API_EXTRAS" ]; then
    uv pip install -e ".[${API_EXTRAS}]" \
      --extra-index-url https://pytorch.org \
      --index-strategy unsafe-best-match
  else
    uv pip install -e . --index-strategy unsafe-best-match
  fi

else
  echo "!!codx-junior API already installed at ${CODX_JUNIOR_API_VENV}"
fi
