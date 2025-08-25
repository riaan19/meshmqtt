#!/bin/bash

# This script automates the secure installation of the MeshMQTT project on a Raspberry Pi.
# It sets up a virtual environment in the user's home directory, installs dependencies,
# and configures a systemd service to run on startup as the current user.
# The script ensures all files are owned by the user for security, and requires sudo for system changes.

set -e  # Exit on any error

# Get the logged-in user's username (not root, even with sudo)
CURRENT_USER=${SUDO_USER:-$(who -u | awk '{print $1}')}
if [ -z "$CURRENT_USER" ] || [ "$CURRENT_USER" = "root" ]; then
    echo "Error: Could not determine the logged-in user. Please run as a non-root user with sudo."
    exit 1
fi
INSTALL_DIR="/home/$CURRENT_USER/meshmqtt"
VENV_DIR="$INSTALL_DIR/venv"
LOG_FILE="/tmp/meshmqtt_install.log"

# Function to log messages
log_message() {
    echo "$1" | tee -a "$LOG_FILE"
}

# Function to check for internet connectivity
check_internet() {
    log_message "Checking internet connectivity..."
    if ! ping -c 1 google.com &> /dev/null; then
        log_message "Error: No internet connection. Please ensure the Raspberry Pi is connected to the internet."
        exit 1
    fi
}

# Function to check command success
check_status() {
    if [ $? -ne 0 ]; then
        log_message "Error: $1"
        exit 1
    fi
}

# Initialize log file
> "$LOG_FILE"
log_message "Starting MeshMQTT installation for user $CURRENT_USER..."

# Check internet connectivity
check_internet

# Update package list and install prerequisites
log_message "Updating package list and installing prerequisites..."
sudo apt update
check_status "Failed to update package list"
sudo apt install -y git python3-venv libffi-dev libssl-dev
check_status "Failed to install system dependencies"

# Check if the installation directory exists
if [ -d "$INSTALL_DIR" ]; then
    log_message "Directory $INSTALL_DIR already exists. Removing for a clean install..."
    sudo rm -rf "$INSTALL_DIR"
    check_status "Failed to remove existing $INSTALL_DIR"
fi

# Clone the repository to the user's home directory as the user
log_message "Cloning MeshMQTT repository..."
sudo -u "$CURRENT_USER" git clone https://github.com/riaan19/meshmqtt.git "$INSTALL_DIR"
check_status "Failed to clone repository"

# Ensure ownership and permissions for security
sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"
sudo chmod -R u+rwX,go-rwx "$INSTALL_DIR"  # User read/write/execute, no group/other access for security
check_status "Failed to set ownership and secure permissions of $INSTALL_DIR"

# Change to the project directory
cd "$INSTALL_DIR"
check_status "Failed to change to $INSTALL_DIR"

# Create and activate virtual environment as the user
log_message "Creating and activating virtual environment..."
sudo -u "$CURRENT_USER" python3 -m venv "$VENV_DIR"
check_status "Failed to create virtual environment"
sudo -u "$CURRENT_USER" chmod -R u+rwX "$VENV_DIR"
check_status "Failed to set permissions for virtual environment"

# Ensure pip is available in the virtual environment
if [ ! -f "$VENV_DIR/bin/pip" ]; then
    log_message "Error: pip not found in virtual environment at $VENV_DIR/bin/pip"
    exit 1
fi

# Install Python dependencies as the user using the virtual environment's pip
log_message "Installing Python dependencies..."
sudo -u "$CURRENT_USER" "$VENV_DIR/bin/pip" install --upgrade pip
check_status "Failed to upgrade pip in virtual environment"
sudo -u "$CURRENT_USER" "$VENV_DIR/bin/pip" install meshtastic paho-mqtt flask
check_status "Failed to install Python dependencies"

# Ensure final ownership and secure permissions
sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"
sudo chmod -R u+rwX,go-rwx "$INSTALL_DIR"
check_status "Failed to set final ownership and secure permissions of $INSTALL_DIR"

# Create systemd service file for auto-start
log_message "Creating systemd service file..."
sudo tee /etc/systemd/system/meshmqtt.service > /dev/null << EOF
[Unit]
Description=MeshMQTT Dashboard Service
After=network-online.target
Wants=network-online.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/mesh_dashboard.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
check_status "Failed to create systemd service file"

# Reload systemd, enable and start the service
log_message "Configuring and starting meshmqtt service..."
sudo systemctl daemon-reload
check_status "Failed to reload systemd"
sudo systemctl enable meshmqtt.service
check_status "Failed to enable meshmqtt service"
sudo systemctl start meshmqtt.service
check_status "Failed to start meshmqtt service"

# Check the service status
log_message "Checking service status..."
sudo systemctl status meshmqtt.service | tee -a "$LOG_FILE"

log_message "Installation complete. The meshmqtt service is now running and set to start on boot."
log_message "All files are securely owned by $CURRENT_USER with no group/other access."
log_message "If you need to configure config.json or other files, edit them in $INSTALL_DIR."
log_message "The dashboard is accessible via http://$(hostname -I | awk '{print $1}'):5000 (check mesh_dashboard.py for port details)."
log_message "Update MQTT server information on web interface and run"
log_message "sudo systemctl restart meshmqtt.service"
log_message "Installation log saved to $LOG_FILE."
