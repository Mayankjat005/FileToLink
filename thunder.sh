#!/bin/bash
source "$(dirname "$0")/venv/bin/activate"
python update.py && python -m Thunder
