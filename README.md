# Smite - Tunneling Control Panel

<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/SmiteD.png"/>
    <source media="(prefers-color-scheme: light)" srcset="assets/SmiteL.png"/>
    <img src="assets/SmiteL.png" alt="Smite Logo" width="200"/>
  </picture>
  
  **Modern tunnel management built on GOST, Backhaul, Rathole, Chisel, and FRP, featuring dual-node architecture, intuitive WebUI, real-time status tracking, and open-source freedom.**
  
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
  [![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
  [![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688.svg)](https://fastapi.tiangolo.com/)
  [![React](https://img.shields.io/badge/React-18+-61DAFB.svg)](https://reactjs.org/)
  [![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-3178C6.svg)](https://www.typescriptlang.org/)
  [![Docker](https://img.shields.io/badge/Docker-24.0+-2496ED.svg)](https://www.docker.com/)
  [![Nginx](https://img.shields.io/badge/Nginx-1.25+-009639.svg)](https://www.nginx.com/)
  [![SQLite](https://img.shields.io/badge/SQLite-3.42+-003B57.svg)](https://www.sqlite.org/)
  [![Architecture](https://img.shields.io/badge/Architecture-amd64%20%7C%20arm64-orange.svg)](https://github.com/zZedix/Smite)
</div>

---

## 🚀 Features

- **Multiple Tunnel Types**: Support for TCP, UDP, WebSocket, gRPC, TCPMux via GOST, Backhaul, Rathole, Chisel, and FRP
- **Unified Node Management**: Iran and Foreign nodes are manageable from a single panel for reverse tunnels
- **Web UI**: Modern, intuitive web interface with real-time connection status tracking
- **CLI Tools**: Powerful command-line tools for management
- **Telegram Bot**: Panel statistics and automatic backups via Telegram
- **GOST Forwarding**: Forward traffic from Iran nodes to Foreign servers with support for TCP, UDP, WebSocket, gRPC, and TCPMux
- **Multi-Architecture**: Native support for both x86_64 (amd64) and ARM64 (aarch64) servers

---

## 📋 Prerequisites

- Docker and Docker Compose installed
- **Supported architectures**: x86_64 (amd64), ARM64 (aarch64)
- For Iran servers, install Docker first:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/manageitir/docker/main/install-ubuntu.sh | sh
  ```

---

## 🔧 Panel Installation

### Quick Install

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/zZedix/Smite/main/scripts/install.sh)"
```

<details>
<summary><strong>Manual Install</strong></summary>

1. Clone the repository:
```bash
git clone https://github.com/zZedix/Smite.git
cd Smite
```

2. Copy environment file and configure:
```bash
cp .env.example .env
# Edit .env with your settings
```

3. Install CLI tools:
```bash
sudo bash cli/install_cli.sh
```

4. Start services:
```bash
docker compose up -d
```

5. Create admin user:
```bash
smite admin create
```

6. Access the web interface at `http://localhost:8000`

</details>

---

## 🖥️ Node Installation

### Architecture

- **Iran Nodes**: Handle reverse tunnels (Rathole, Backhaul, Chisel, FRP) and run GOST forwarders
- **Foreign Nodes**: Participate in reverse tunnels and receive forwarded traffic from Iran nodes

### Quick Install

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/zZedix/Smite/main/scripts/smite-node.sh)"
```

<details>
<summary><strong>Manual Install</strong></summary>

1. Navigate to node directory:
```bash
cd node
```

2. Copy Panel CA certificate:
```bash
mkdir -p certs
# For Iran nodes, use ca.crt
cp /path/to/panel/ca.crt certs/ca.crt
# For Foreign servers, use ca-server.crt
# cp /path/to/panel/ca-server.crt certs/ca.crt
```

3. Create `.env` file:
```bash
cat > .env << EOF
NODE_API_PORT=8888
NODE_NAME=node-1
PANEL_CA_PATH=/etc/smite-node/certs/ca.crt
PANEL_ADDRESS=panel.example.com:443
EOF
```

> **Note**: The panel validates node roles during registration. Each node must have a consistent role (iran or foreign) to prevent conflicts.

4. Start node:
```bash
docker compose up -d
```

</details>

---

## 🛠️ CLI Tools

### Panel CLI (`smite`)

**Admin Management:**
```bash
smite admin create      # Create admin user
smite admin update      # Update admin password
```

**Panel Management:**
```bash
smite status            # Show system status
smite update            # Update panel (pull images and recreate)
smite restart           # Restart panel (recreate to pick up .env changes)
smite logs              # View panel logs
```

**Configuration:**
```bash
smite edit              # Edit docker-compose.yml
smite edit-env          # Edit .env file
```

### Node CLI (`smite-node`)

**Node Management:**
```bash
smite-node status       # Show node status
smite-node update       # Update node (pull images and recreate)
smite-node restart      # Restart node (recreate to pick up .env changes)
smite-node logs         # View node logs
```

**Configuration:**
```bash
smite-node edit         # Edit docker-compose.yml
smite-node edit-env     # Edit .env file
```

---

## 📖 Tunnel Types

### GOST Tunnels (Iran Node Forwarding)
- **TCP**: Simple TCP forwarding
- **UDP**: UDP packet forwarding
- **WebSocket (WS)**: WebSocket protocol forwarding
- **gRPC**: gRPC protocol forwarding
- **TCPMux**: TCP multiplexing for multiple connections

GOST tunnels run on Iran nodes and forward traffic to Foreign servers. When creating a GOST tunnel, specify both an Iran node and a Foreign server. The Iran node will listen on the specified port and forward all traffic to the Foreign server's IP address and port.

### Backhaul Tunnels (Reverse Tunnel)
- **TCP / UDP**: Low-latency reverse tunnels with optional UDP-over-TCP
- **WS / WSMux**: WebSocket transports for CDN-friendly deployments
- **TCPMux**: TCP multiplexing support
- **Advanced Controls**: Configure multiplexing, keepalive, sniffer, and custom port maps per tunnel

The panel automatically configures both Iran and Foreign nodes when creating a tunnel.

### Rathole Tunnels (Reverse Tunnel)
- **TCP**: Standard TCP reverse tunnel
- **WebSocket (WS)**: WebSocket transport support

Rathole tunnels allow you to expose services running on the Foreign node's network through the Iran node.

### Chisel Tunnels (Reverse Tunnel)
Chisel tunnels provide fast TCP reverse tunnel functionality, enabling you to expose services running on the Foreign node's network through the Iran node with high performance.

### FRP Tunnels (Reverse Tunnel)
FRP (Fast Reverse Proxy) tunnels provide reliable TCP/UDP reverse tunnel functionality. FRP supports both TCP and UDP protocols, with optional IPv6 support for tunneling IPv6 traffic over IPv4 networks.

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 💰 Donations

If you find Smite useful and want to support its development, consider making a donation:

### Cryptocurrency Donations

- **Bitcoin (BTC)**: `bc1q637gahjssmv9g3903j88tn6uyy0w2pwuvsp5k0`
- **Ethereum (ETH)**: `0x5B2eE8970E3B233F79D8c765E75f0705278098a0`
- **Tron (TRX)**: `TSAsosG9oHMAjAr3JxPQStj32uAgAUmMp3`
- **USDT (BEP20)**: `0x5B2eE8970E3B233F79D8c765E75f0705278098a0`
- **TON**: `UQA-95WAUn_8pig7rsA9mqnuM5juEswKONSlu-jkbUBUhku6`

### Other Ways to Support

- ⭐ Star the repository if you find it useful
- 🐛 Report bugs and suggest improvements
- 📖 Improve documentation and translations
- 🔗 Share with others who might benefit

---

<div align="center">
  
  **Made with ❤️ by [zZedix](https://github.com/zZedix)**
  
  *Securing the digital world, one line of code at a time!*
  
</div>
