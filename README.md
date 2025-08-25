# MeshMQTT

MeshMQTT integrates Meshtastic LoRa devices with a private MQTT broker and provides a web dashboard for creating sensors in Home Assistant, designed to run on a Raspberry Pi.

## Disclaimer

This project is primarily for my personal use and is shared for reference. Use it at your own risk. There is minimal security in the default setup, and improper use could strain the LoRa network or cause issues with your equipment. Ensure you understand the implications before proceeding.

## Overview

MeshMQTT connects Meshtastic LoRa devices to a private MQTT broker, enabling seamless integration with Home Assistant for home automation. It includes a web dashboard to manage and visualize the creation of sensor data (e.g., binary and numeric sensors) and is optimized for Raspberry Pi deployment.

## Installation

The `install_meshmqtt.sh` script automates the setup process, installing dependencies, creating a secure Python virtual environment in the user's home directory, and configuring a systemd service to run MeshMQTT on boot. All files are owned by the user for security, and logs are saved to `/tmp/meshmqtt_install.log` for troubleshooting.

### Steps to Install

1. **Download the Auto-Install Script**

   - Open a terminal as a non-root user (e.g., `admin` or `pi`).
   - Download the script to your home directory:

     ```bash
     wget -O ~/install_meshmqtt.sh https://raw.githubusercontent.com/riaan19/meshmqtt/main/install_meshmqtt.sh
     ```

2. **Make the Script Executable**

   - Set execute permissions:

     ```bash
     chmod +x ~/install_meshmqtt.sh
     ```

3. **Run the Script with Sudo**

   - Execute the script with sudo to allow system-level changes:

     ```bash
     sudo bash ~/install_meshmqtt.sh
     ```
   - The script runs as the current user (e.g., `admin`), ensuring all files are owned by you and not root. It requires sudo for installing system packages and configuring the systemd service.

4. **What the Script Does**

   - Checks for internet connectivity.
   - Installs system dependencies (`git`, `python3-venv`, `libffi-dev`, `libssl-dev`).
   - Clones the MeshMQTT repository to `~/meshmqtt`.
   - Creates a Python virtual environment at `~/meshmqtt/venv`.
   - Installs Python dependencies (`meshtastic`, `paho-mqtt`, `flask`) in the virtual environment.
   - Secures file permissions (user-only access, no group/other permissions).
   - Creates and enables a systemd service (`meshmqtt.service`) to run `mesh_dashboard.py` on startup as the current user.
   - Starts the service and logs all actions to `/tmp/meshmqtt_install.log`.

5. **Verify the Installation**

   - Check the systemd service status:

     ```bash
     sudo systemctl status meshmqtt.service
     ```
     - Look for `active (running)` in the output.
   - Access the dashboard in a web browser:
     - URL: `http://<raspberry-pi-ip>:5000` (replace `<raspberry-pi-ip>` with your Pi's IP, e.g., `192.168.1.100`).
     - Find your Pi's IP:

       ```bash
       hostname -I
       ```
   - Verify file ownership and permissions:

     ```bash
     ls -ld ~/meshmqtt
     ls -l ~/meshmqtt
     ```
     - Ensure files are owned by your user (e.g., `admin:admin`) with permissions like `drwx------` or `-rw-------`.

6. **Configure MeshMQTT**

   - Edit the configuration file if needed: or do this from the web interface and restart service

     ```bash
     nano ~/meshmqtt/config.json
     ```
     - Update MQTT settings (`mqtt_broker`, `mqtt_port`, `mqtt_username`, `mqtt_password`) to match your MQTT server.
     - Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).
   - Restart the service to apply changes:

     ```bash
     sudo systemctl restart meshmqtt.service
     ```

7. **Check Logs for Issues**

   - View the installation log:

     ```bash
     cat /tmp/meshmqtt_install.log
     ```
   - View the application logs:

     ```bash
     cat ~/meshmqtt/meshtastic_bridge.log
     ```

## Updating

To update MeshMQTT to the latest version:

1. **Navigate to the Repository**

   ```bash
   cd ~/meshmqtt
   ```

2. **Pull the Latest Changes**

   ```bash
   git pull origin main
   ```

   - If conflicts occur, stash or reset local changes:

     ```bash
     git stash
     git pull origin main
     ```

3. **Update Dependencies**

   - Activate the virtual environment and update Python packages:

     ```bash
     source ~/meshmqtt/venv/bin/activate
     pip install --upgrade meshtastic paho-mqtt flask
     deactivate
     ```

4. **Restart the Service**

   ```bash
   sudo systemctl restart meshmqtt.service
   ```

5. **Verify the Update**

   - Check the service status:

     ```bash
     sudo systemctl status meshmqtt.service
     ```

## Configuration

- **Meshtastic Device**: Ensure your LoRa radio is connected via USB (default: `/dev/ttyUSB0`). Update the `SERIAL_PORT` variable in `~/meshmqtt/mesh_dashboard.py` if using a different port (e.g., `/dev/ttyACM0`).
- **MQTT Broker**: Configure MQTT settings in `~/meshmqtt/config.json` to match your broker's details or from the web interface. Changing the mqtt_topic_prefix may cause some issues with other functionsns recommended not to do this. **RESTART THE SERVICE AFTER CHANGING MQTT SERVER DETAILS** or you will run into issues
- **Custom Sensors**: Use the web dashboard (`http://<raspberry-pi-ip>:5000`) to configure sensors (e.g., numeric sensors with pattern `T` for temperature or binary sensors with pattern `detected`).
- **Customizations**: Modify `~/meshmqtt/mesh_dashboard.py` or other files for specific needs, then restart the service.

## Troubleshooting

- **Service Fails to Start**:

  - Check logs:

    ```bash
    cat /tmp/meshmqtt_install.log
    cat ~/meshmqtt/meshtastic_bridge.log
    journalctl -u meshmqtt.service
    ```
  - Ensure the Meshtastic device is connected and the serial port is correct in `mesh_dashboard.py`.
  - Verify `mesh_dashboard.py` exists and has no syntax errors.

- **Dashboard Not Accessible**:

  - Confirm the Pi's IP address (`hostname -I`) and port (`5000` by default).
  - Check firewall settings:

    ```bash
    sudo ufw status
    sudo ufw allow 5000
    ```
  - Ensure the service is running (`sudo systemctl status meshmqtt.service`).

- **MQTT Issues**:

  - Verify MQTT broker settings in `~/meshmqtt/config.json`.
  - Test connectivity:

    ```bash
    mosquitto_sub -h <broker> -p <port> -u <username> -P <password> -t "Mesh/feeds/#"
    ```
  - Ensure the MQTT broker is running and accessible.

- **Dependency Issues**:

  - Rerun system dependency installation:

    ```bash
    sudo apt install -y libffi-dev libssl-dev
    ```
  - Reinstall Python dependencies:

    ```bash
    source ~/meshmqtt/venv/bin/activate
    pip install --upgrade meshtastic paho-mqtt flask
    deactivate
    ```

- **pip Not Found**:

  - Check for the virtual environment's `pip`:

    ```bash
    ls -l ~/meshmqtt/venv/bin/pip
    ```
  - If missing, recreate the virtual environment:

    ```bash
    rm -rf ~/meshmqtt/venv
    python3 -m venv ~/meshmqtt/venv
    ~/meshmqtt/venv/bin/pip install --upgrade pip
    ~/meshmqtt/venv/bin/pip install meshtastic paho-mqtt flask
    ```

## Notes

- The script assumes `mesh_dashboard.py` is the main script and installs to `~/meshmqtt`. Update `/etc/systemd/system/meshmqtt.service` if the main script name changes.
- The virtual environment (`~/meshmqtt/venv`) isolates Python dependencies, preventing conflicts with system packages.
- The systemd service runs as the installing user (e.g., `admin` or `pi`), not root, for enhanced security.
- For Raspberry Pi Zero or older models, ensure sufficient memory (consider increasing swap space) for dependency installation.
- If using a non-Raspberry Pi OS, modify the script's package installation commands (e.g., use `dnf` for Fedora-based systems).
- Regularly check for updates to the MeshMQTT repository to get the latest features and fixes.

If you encounter issues, look at  the output of:

- `sudo systemctl status meshmqtt.service`
- `/tmp/meshmqtt_install.log`
- `~/meshmqtt/meshtastic_bridge.log`

This will help diagnose any problems with the installation or runtime.
