```markdown
# MeshMQTT

MeshMQTT is a project that integrates Meshtastic with MQTT and provides a web dashboard for monitoring and control on a Raspberry Pi.

## Prerequisites

- **Raspberry Pi**: Models 2, 3, 4, 5, Zero, or Zero 2 W (Pi 4/5 recommended for best performance).
- **Operating System**: Raspberry Pi OS (32-bit or 64-bit recommended).
- **Meshtastic Device**: A compatible LoRa radio connected via USB or serial.
- **Internet Connection**: Required for downloading dependencies and cloning the repository.
- **Storage**: At least 2 GB of free space on the SD card.
- **User Permissions**: Access to a user account with `sudo` privileges.

## Installation

The `install_meshmqtt.sh` script automates the setup process, installing dependencies, setting up a virtual environment, and configuring the service to run on boot. It dynamically uses the current logged-in user's home directory.

### Steps to Install

1. **Download the Repository** (if not already cloned):
   ```bash
   git clone https://github.com/riaan19/meshmqtt.git
   cd meshmqtt
   ```

2. **Make the Script Executable**:
   ```bash
   chmod +x install_meshmqtt.sh
   ```

3. **Run the Installation Script**:
   ```bash
   sudo ./install_meshmqtt.sh
   ```

4. **Verify the Service**:
   - Check the service status:
     ```bash
     sudo systemctl status meshmqtt.service
     ```
   - View logs for debugging:
     ```bash
     journalctl -u meshmqtt.service
     ```

5. **Access the Dashboard**:
   - Open a browser and navigate to `http://<raspberry-pi-ip>:5000` (replace `<raspberry-pi-ip>` with your Pi's IP address).
   - Check `mesh_dashboard.py` for the exact port if different.

## Configuration

- **Meshtastic Device**: Ensure your LoRa radio is connected via USB or serial. Update `config.json` (if used) with the correct serial port or device settings.
- **MQTT Broker**: Configure MQTT settings in `config.json` or the main script if required.
- **Customizations**: Edit `mesh_dashboard.py` or other files in `~/meshmqtt` (your home directory) for specific configurations.

## Troubleshooting

- **Service Fails to Start**:
  - Check logs: `journalctl -u meshmqtt.service`
  - Ensure the Meshtastic device is connected and the serial port is correct.
  - Verify `mesh_dashboard.py` exists and is error-free.
- **Dependency Issues**:
  - Rerun `sudo apt install -y libffi-dev libssl-dev` and `pip install` commands in the virtual environment.
- **Dashboard Not Accessible**:
  - Confirm the Pi's IP address (`hostname -I`) and port.
  - Check firewall settings or network connectivity.

## Notes

- The script assumes `mesh_dashboard.py` is the main script. Update the systemd service file (`/etc/systemd/system/meshmqtt.service`) if your main script has a different name.
- The script uses the current logged-in user's home directory (e.g., `/home/<your-username>/meshmqtt`) for installation.
- For Raspberry Pi Zero or older models, ensure sufficient memory (consider increasing swap space) to handle dependency installation.
- If using a non-Raspberry Pi OS, modify the script's package installation commands (e.g., use `dnf` for Fedora-based systems).

For issues or contributions, please open an issue or pull request on the [GitHub repository](https://github.com/riaan19/meshmqtt).
```