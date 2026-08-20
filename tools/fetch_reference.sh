#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination="${repo_root}/.reference/kaggle-environments"
revision="28b6d8af3ce73926b3d0fda1410c1ddd8384ab8c"

if [[ ! -d "${destination}/.git" ]]; then
  mkdir -p "$(dirname "${destination}")"
  git clone --filter=blob:none --no-checkout \
    https://github.com/Kaggle/kaggle-environments.git "${destination}"
fi

git -C "${destination}" fetch --depth 1 origin "${revision}"
git -C "${destination}" checkout --detach "${revision}"
git -C "${destination}" rev-parse HEAD

