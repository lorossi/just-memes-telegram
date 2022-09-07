#!/bin/bash
cd /home/pi/scripts/just-memes-telegram
source venv/bin/activate
python3 just-memes-telegram.py &
deactivate
