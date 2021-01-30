#!/bin/bash
set -ex

DEST_DIR="build/contents"
rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"
pip3 install -r "requirements.txt" --no-deps "./" --target "${DEST_DIR}"
python3 -m zipapp -c -m "blkcapteng.__main__:main" -p "/usr/bin/env python3" -o build/blkcapteng $DEST_DIR 
chmod +x build/blkcapteng
