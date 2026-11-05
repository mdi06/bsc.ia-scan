#!/bin/bash
# ==============================================================================
# Agent ID Monitor - Local Launch Script
# Runs the entire scanner and API server 100% locally on your Mac.
# Costs $0.00 and uses zero cloud bandwidth.
# ==============================================================================

cd "$(dirname "$0")"

echo "======================================================================"
echo "🚀 Starting Agent ID Monitor (Local Machine Mode)"
echo "======================================================================"
echo "📡 Live Radar & Scanner: Starting on your Mac..."
echo "🌐 Web Dashboard:         http://localhost:8080"
echo "💸 Cloud Cost:            $0.00 (Zero Render bandwidth used)"
echo "🛑 To stop:               Press Ctrl + C in this terminal window"
echo "======================================================================"
echo ""

# Auto-open browser after 2 seconds
(sleep 2 && open "http://localhost:8080") &

# Start backend server + worker
python3 server.py
