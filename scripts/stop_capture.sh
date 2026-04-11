#!/bin/bash
cd ~/agentbot-platform

if [ -f logs/capture.pid ]; then
    PID=$(cat logs/capture.pid)
    kill $PID 2>/dev/null
    echo "Stopped capture (PID $PID)"
    rm logs/capture.pid
else
    pkill -f "python main.py" 2>/dev/null
    echo "Stopped capture"
fi
