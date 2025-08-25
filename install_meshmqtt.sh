#!/bin/bash

# This script automates the installation of the MeshMQTT project on a Raspberry Pi.
# It sets up a virtual environment, installs dependencies, and configures the main script
# to run on startup using systemd. It uses the logged-in user's username for paths and
# service configuration, even when run with sudo.

set -e  # Exit on any error

# Get the logged-in user's username (not root, even with sudo)
CURRENT_USER=${SUDO_USER:-$(who -u | awk '{print $1}')}
if [ -z "$CURRENT_USER" ] || [ "$CURRENT_USER" = "root" ]; then
    echo "Error: Could not determine the logged-in user. Please run as a non-root user with sudo."
    exit 1
fi
INSTALL_DIR="/home/$CURRENT_USER/meshmqtt"

# Function to check for internet connectivity
check_internet() {
    echo "Checking internet connectivity..."
    if ! ping -c 1 google.com &> /dev/null; then
        echo "Error: No internet connection. Please ensure the Raspberry Pi is connected to the internet."
        exit 1
    fi
}

# Function to check command success
check_status() {
    if [ $? -ne 0 ]; then
        echo "Error: $1"
        exit 1
    fi
}

# Check internet connectivity
check_internet

# Update package list and install prerequisites
echo "Updating package list and installing prerequisites..."
sudo apt update
check_status "Failed to update package list"
sudo apt install -y git python3-venv libffi-dev libssl-dev
check_status "Failed to install system dependencies"

# Check if the installation directory exists
if [ -d "$INSTALL_DIR" ]; then
    echo "Directory $INSTALL_DIR already exists. Skipping git clone."
else
    # Clone the repository to the current user's home directory
    echo "Cloning MeshMQTT repository..."
    git clone https://github.com/riaan19/meshmqtt.git "$INSTALL_DIR"
    check_status "Failed to clone repository"
fi

# Change ownership to current user
sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"
check_status "Failed to set ownership of $INSTALL_DIR"

# Change to the project directory
cd "$INSTALL_DIR"
check_status "Failed to change to $INSTALL_DIR"

# Create and activate virtual environment
echo "Creating and activating virtual environment..."
python3 -m venv venv
check_status "Failed to create virtual environment"
source venv/bin/activate
check_status "Failed to activate virtual environment"

# Install Python dependencies
echo "Installing Python dependencies..."
pip install meshtastic paho-mqtt flask
check_status "Failed to install Python dependencies"

# Deactivate the virtual environment
deactivate

# Create systemd service file for auto-start
echo "Creating systemd service file..."
sudo tee /etc/systemd/system/meshmqtt.service > /dev/null << EOF
[Unit]
Description=MeshMQTT Dashboard Service
After=multi-user.target

[Service]
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/mesh_dashboard.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF
check_status "Failed to create systemd service file"

# Reload systemd, enable and start the service
echo "Configuring and starting meshmqtt service..."
sudo systemctl daemon-reload
check_status "Failed to reload systemd"
sudo systemctl enable meshmqtt.service
check_status "Failed to enable meshmqtt service"
sudo systemctl start meshmqtt.service
check_status "Failed to start meshmqtt service"

# Check the service status
echo "Checking service status..."
sudo systemctl status meshmqtt.service

echo "Installation complete. The meshmqtt service is now running and set to start on boot."
echo "If you need to configure config.json or other files, edit them in $INSTALL_DIR."
echo "The dashboard is accessible via http://$(hostname -I | awk '{print $1}'):5000 (check mesh_dashboard.py for port details)."
