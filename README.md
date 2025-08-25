MeshMQTT

MeshMQTT is a project that integrates Meshtastic with MQTT and provides a web interdace to add sensors in HomeAssistant on a Raspberry Pi.

Prerequisites





Raspberry Pi: Models 2, 3, 4, 5, Zero, or Zero 2 W (Pi 4/5 recommended for best performance).



Operating System: Raspberry Pi OS (32-bit or 64-bit recommended).



Meshtastic Device: A compatible LoRa radio connected via USB or serial.



Internet Connection: Required for downloading dependencies and cloning the repository.



Storage: At least 2 GB of free space on the SD card.



User Permissions: Access to the pi user account with sudo privileges.

Installation

The install_meshmqtt.sh script automates the setup process, installing dependencies, setting up a virtual environment, and configuring the service to run on boot.

Steps to Install





Download the Repository (if not already cloned):

git clone https://github.com/riaan19/meshmqtt.git
cd meshmqtt



Make the Script Executable:

chmod +x install_meshmqtt.sh



Run the Installation Script:

sudo ./install_meshmqtt.sh



Verify the Service:





Check the service status:

sudo systemctl status meshmqtt.service



View logs for debugging:

journalctl -u meshmqtt.service



Access the Dashboard:





Open a browser and navigate to http://<raspberry-pi-ip>:5000 (replace <raspberry-pi-ip> with your Pi's IP address).



Check mesh_dashboard.py for the exact port if different.

Configuration





Meshtastic Device: Ensure your LoRa radio is connected via USB or serial. Update config.json (if used) with the correct serial port or device settings.



MQTT Broker: Configure MQTT settings in config.json or the main script if required.



Customizations: Edit mesh_dashboard.py or other files in /home/pi/meshmqtt for specific configurations.
