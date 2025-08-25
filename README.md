# MeshMQTT

A lightweight MQTT mesh bridge for integrating Meshtastic networks with Home Assistant.

## Overview

MeshMQTT is a tool designed to bridge messages between a Meshtastic mesh network and an MQTT broker, enabling seamless integration with Home Assistant for home automation and monitoring. This project allows Meshtastic nodes to communicate over MQTT, facilitating real-time data exchange and control within your smart home ecosystem.

## Features

- **Meshtastic Integration**: Connects Meshtastic mesh networks to an MQTT broker.
- **Home Assistant Compatibility**: Publishes mesh data to MQTT topics for easy integration with Home Assistant.
- **Configurable**: Supports custom MQTT server settings and channel configurations.
- **Lightweight**: Minimal resource usage for running on various platforms.

## Requirements

- Python 3.8 or higher
- Meshtastic device with MQTT module enabled
- MQTT broker (e.g., Mosquitto)
- Home Assistant (optional, for HA integration)
- Required Python libraries:
  - `meshtastic`
  - `paho-mqtt`
  - `cryptography` (for encrypted channels)

Install dependencies using:
```bash
pip install meshtastic paho-mqtt cryptography
```

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/riaan19/meshmqtt.git
   cd meshmqtt
   ```

2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure your Meshtastic device to enable MQTT (see [Meshtastic MQTT Configuration](https://meshtastic.org/docs/configuration/mqtt/)).

## Usage

1. Configure your MQTT broker settings in `config.json` (example provided in the repository).
2. Run the bridge:
   ```bash
   python meshmqtt.py
   ```

### Example Configuration

Create a `config.json` file with the following structure:

```json
{
  "mqtt": {
    "host": "your.mqtt.broker",
    "port": 1883,
    "username": "your_username",
    "password": "your_password",
    "root_topic": "msh/US"
  },
  "meshtastic": {
    "channel": "LongFast",
    "uplink_enabled": true,
    "downlink_enabled": true
  }
}
```

- `host`: Address of your MQTT broker.
- `port`: MQTT broker port (default: 1883).
- `username` and `password`: Credentials for the MQTT broker.
- `root_topic`: MQTT root topic (e.g., `msh/US`).
- `channel`: Meshtastic channel name.
- `uplink_enabled` and `downlink_enabled`: Enable/disable message forwarding.

## Home Assistant Integration

To integrate with Home Assistant:

1. Ensure your MQTT broker is configured in Home Assistant.
2. Add sensors or automations in Home Assistant to subscribe to the MQTT topics (e.g., `msh/US/2/json/LongFast/#`).
3. Use Home Assistant to visualize or act on Meshtastic data (e.g., node telemetry, messages).

## Contributing

Contributions are welcome! To contribute:

1. Fork the repository.
2. Create a new branch (`git checkout -b feature/your-feature`).
3. Commit your changes (`git commit -m "Add your feature"`).
4. Push to the branch (`git push origin feature/your-feature`).
5. Open a pull request.

Please ensure your code follows the project's coding style and includes tests where applicable.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Meshtastic](https://meshtastic.org) for the open-source mesh networking protocol.
- [Home Assistant](https://www.home-assistant.io) for the home automation platform.
- Community contributors for feedback and support.