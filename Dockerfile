# Dockerfile para el Backend
FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (excluding credentials for security)
COPY main.py .
COPY start.sh .
# Note: We don't copy the whiteboard.json file for security reasons
# The service will use Google Cloud's built-in authentication

# Make the startup script executable
RUN chmod +x /app/start.sh

# Use the startup script
CMD ["/app/start.sh"]
