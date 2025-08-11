#!/bin/bash
set -e

# Get the port from environment variable, default to 8080
PORT=${PORT:-8080}

echo "Starting backend service on port $PORT"

# Start the FastAPI application
exec uvicorn main:app --host 0.0.0.0 --port $PORT
