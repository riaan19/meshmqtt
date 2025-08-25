```bash
#!/bin/bash

# This script automates the installation of the meshmqtt project on a Raspberry Pi.
# It clones the repo, sets up a virtual environment, installs dependencies,
# and configures the main script to run on startup using systemd.
# It uses the current logged-in user's username for paths and service configuration.

# Get the current user's username
CURRENT_USER=$(whoami)

# Update package list and install prerequisites
sudo apt update
sudo apt install -y git python3-venv libffi-dev libssl-dev

# Clone the repository to the current user's home directory
git clone https://github.com/riaan19/meshmqtt.git /home/$CURRENT_USER/meshmqtt

# Change to the project directory
cd /home/$CURRENT_USER/meshmqtt

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install meshtastic paho-mqtt flask

# Deactivate the virtual environment
deactivate

# Create systemd service file for auto-start
sudo bash -c "cat << EOF > /etc/systemd/system/meshmqtt.service
[Unit]
Description=MeshMQTT Dashboard Service
After=multi-user.target

[Service]
User=$CURRENT_USER
WorkingDirectory=/home/$CURRENT_USER/meshmqtt
ExecStart=/home/$CURRENT_USER/meshmqtt/venv/bin/python /home/$CURRENT_USER/meshmqtt/mesh_dashboard.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF"

# Reload systemd, enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable meshmqtt.service
sudo systemctl start meshmqtt.service

# Check the status
sudo systemctl status meshmqtt.service

echo "Installation complete. The meshmqtt service is now running and set to start on boot."
echo "If you need to configure config.json or other files, edit them in /home/$CURRENT_USER/meshmqtt."
echo "The dashboard is accessible via http://<pi-ip>:5000 (check mesh_dashboard.py for port details)."
```