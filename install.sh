#!/usr/bin/env bash
# Set up PaperScan (macOS). Run from a clone, it sets up that folder. Run directly from the web,
# it clones the repository into ~/PaperScan first (and reuses that clone when run again):
#
#   curl -fsSL https://raw.githubusercontent.com/bjspi/PaperScan/main/install.sh | bash
#
# Creates a virtual environment in .venv (Python 3.12-3.13), installs PaperScan with all
# extras, links the paperscan command into ~/.local/bin and offers to install the external
# tools (Tesseract, cjpegli via Homebrew, adb). Safe to run again.
#
# Uses uv if it is installed (uv also downloads Python 3.13 if needed), otherwise a Python on PATH.
#
#   ./install.sh                         install, set up tools and start
#   ./install.sh --no-start --skip-tools install only
set -euo pipefail

APP_NAME="PaperScan"
REPOSITORY="https://github.com/bjspi/PaperScan.git"
COMMAND_FOLDER="$HOME/.local/bin"
NO_START=0
SKIP_TOOLS=0

parse_arguments() {
  for argument in "$@"; do
    case "$argument" in
      --no-start) NO_START=1 ;;
      --skip-tools) SKIP_TOOLS=1 ;;
      *) echo "Unknown option: $argument" >&2; exit 2 ;;
    esac
  done
}

enter_project_folder() {
  # This script's folder in a clone; otherwise ~/PaperScan, cloned if needed.
  local script_folder=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    script_folder="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  if [ -n "$script_folder" ] && [ -f "$script_folder/pyproject.toml" ]; then
    cd "$script_folder"
    return
  fi
  local target="$HOME/$APP_NAME"
  if [ -d "$target/.git" ]; then
    echo "==> Using the existing clone in $target"
  elif [ -e "$target" ]; then
    echo "$target exists but is not a clone of $APP_NAME. Move it away and run this again." >&2
    exit 1
  else
    command -v git >/dev/null || { echo "Git is required: xcode-select --install" >&2; exit 1; }
    echo "==> Cloning $APP_NAME into $target"
    git clone --quiet "$REPOSITORY" "$target"
  fi
  cd "$target"
}

find_python() {
  # Print the first Python 3.12 or 3.13 on PATH.
  local candidate
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info[:2] not in ((3, 12), (3, 13)))'; then
      command -v "$candidate"
      return
    fi
  done
}

create_environment() {
  if [ -x .venv/bin/python ]; then
    echo "==> Using the existing virtual environment"
    return
  fi
  if command -v uv >/dev/null; then
    # uv only provides the interpreter; the venv module creates the environment (same as on Windows).
    local uv_python
    uv_python="$(uv python find --system 3.13 2>/dev/null || { uv python install 3.13 >&2 && uv python find --system 3.13; } || true)"
    if [ -n "$uv_python" ]; then
      echo "==> Creating the virtual environment with $uv_python"
      "$uv_python" -m venv .venv && return
    fi
    echo "uv could not provide Python 3.13. Looking for a Python instead." >&2
  fi
  local python
  python="$(find_python)"
  if [ -z "$python" ]; then
    echo "No Python 3.12-3.13 found. Install one (brew install python@3.13) or uv (brew install uv)." >&2
    exit 1
  fi
  echo "==> Creating the virtual environment with $python"
  "$python" -m venv .venv
}

stop_running_app() {
  # Update cleanly: close a running instance of this installation first.
  if pgrep -f "$PWD/.venv/bin/paperscan" >/dev/null 2>&1; then
    echo "==> Closing the running $APP_NAME so it can be updated"
    pkill -f "$PWD/.venv/bin/paperscan" || true
    sleep 1
  fi
}

install_package() {
  echo "==> Installing PaperScan and its dependencies"
  .venv/bin/python -m pip install --disable-pip-version-check -e ".[all]"
}

add_command() {
  echo "==> Linking the paperscan command into $COMMAND_FOLDER"
  mkdir -p "$COMMAND_FOLDER"
  ln -sf "$PWD/.venv/bin/paperscan" "$COMMAND_FOLDER/paperscan"
  case ":$PATH:" in
    *":$COMMAND_FOLDER:"*) ;;
    *) echo "    Add $COMMAND_FOLDER to your PATH, e.g.: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
  esac
}

main() {
  parse_arguments "$@"
  enter_project_folder
  create_environment
  stop_running_app
  install_package
  add_command
  if [ "$SKIP_TOOLS" -eq 0 ]; then
    echo "==> Checking the external tools (Tesseract, cjpegli, adb)"
    # Piped through curl, stdin is the script; read answers from the terminal instead.
    .venv/bin/paperscan setup </dev/tty || echo "Some tools are still missing; run 'paperscan setup' later." >&2
  fi
  .venv/bin/paperscan doctor || true
  if [ "$NO_START" -eq 0 ]; then
    echo "==> Starting $APP_NAME"
    nohup .venv/bin/paperscan gui >/dev/null 2>&1 &
  fi
  echo "Done. From now on: paperscan in a new terminal."
}

main "$@"
