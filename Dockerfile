FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency installation
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY main.py .

# Install Python dependencies
RUN uv pip compile pyproject.toml > requirements.txt && \
    uv pip install --system --no-cache -r requirements.txt && \
    rm requirements.txt

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Run the server
CMD ["python", "main.py"] 