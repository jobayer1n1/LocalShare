<h1 align = "center">
🌐 LocalShare — Secure LAN File Sharing
</h1>

<div align="center">

![License](https://img.shields.io/badge/License-MIT-D6C0B3?style=for-the-badge&logo=bookstack&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.7%2B-F1F3E0?style=for-the-badge&logo=python&logoColor=blue)
![Platform](https://img.shields.io/badge/Platform-Cross--Platform-BBD8A3?style=for-the-badge&logo=devices&logoColor=white)

</div>

It is a lightweight, cross-platform Python solution for seamless file sharing across devices on the same local network. Share files instantly without complex setup, accounts, or external dependencies.

## ✨ Features
---

- **🖥️ Cross-Platform Compatibility** - Windows, Linux, macOS, and Android (Termux) support
- **📁 Smart Directory Management** - Auto-detects and creates OS-appropriate shared folders
- **🌐 Web-Based Interface** - Access files from any modern browser
- **🔒 Security Options** - Optional PIN protection for uploads and deletions
- **⚡ Zero Configuration** - Works out of the box with default settings
- **🔄 Self-Updating** - Built-in update mechanism to stay current
- **🗑️ Controlled File Management** - Optional delete functionality with `--allow-delete` flag

---

## 🚀 Quick Start

### Method 1: Direct Download (Recommended)
- Download and run in one command
```bash
curl -O https://raw.githubusercontent.com/jobayer1n1/LocalShare/main/LocalShare.py && python LocalShare.py
```

### Method 2: Clone Repository
```bash
git clone https://github.com/jobayer1n1/LocalShare.git
cd LocalShare
python LocalShare.py
```

---

## 💻 Usage Examples

| Command | Description |
|---------|-------------|
| `python LocalShare.py` | Start with default settings |
| `python LocalShare.py --dir /path/to/folder` | Use custom shared directory |
| `python LocalShare.py --port 8080` | Run on specific port |
| `python LocalShare.py --pin 1234` | Enable PIN protection |
| `python LocalShare.py --allow-delete` | Enable file deletion |
| `python LocalShare.py --update` | Update to latest version |

---

## 🌍 Network Access

Once running, the script displays access URLs:
```
Local access: http://localhost:5000
Network access: http://192.168.1.100:5000
```

Open the network URL in any browser on devices connected to the same network.

---

## 📂 Default Shared Directories

| Platform | Default Path |
|----------|--------------|
| **Windows** | `C:\Users\<Username>\LocalShare` |
| **Linux/macOS** | `~/LocalShare` |
| **Android (Termux)** | `~/storage/shared/LocalShare` |

*Directories are automatically created if they don't exist.*

---

## 🔒 Security Features

- **Local Network Only** - Files remain within your local network
- **PIN Protection** - Optional authentication for upload and delete operations
- **Controlled Access** - Granular permissions for different operations
- **No External Exposure** - Service not accessible from the internet by default

> **Important**: For internet-facing sharing, implement proper security measures like reverse proxy with authentication or VPN.

---

## 📋 Prerequisites

- **Python 3.7** or higher
- **Flask** web framework

### Install Flask
```bash
pip install flask
```

---

## 🔄 Maintenance

### Update to Latest Version
```bash
python LocalShare.py --update
```

This command automatically fetches and replaces the current script with the latest version from the GitHub repository.

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## ⚠️ Disclaimer

This tool is designed for trusted local networks. Users are responsible for implementing appropriate security measures for their specific use cases.

---
