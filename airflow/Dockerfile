FROM apache/airflow:2.8.1-python3.11

USER root

# Install system dependencies for industrial protocols and scientific computing
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libgomp1 \
    libffi-dev \
    libssl-dev \
    curl \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# Copy and install Python requirements
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh

# Set working directory for veritas source
ENV PYTHONPATH="/opt/airflow/veritas_src:${PYTHONPATH}"
