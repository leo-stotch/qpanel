# qPanel

qPanel is a web-based management tool for qBittorrent, designed to automate torrent management tasks such as:

- applying seeding rules (i.e. `if a torrent has tag noHL, set seeding time to 43200 minutes`);
- finding torrents without hard links;
- pausing cross-seeded torrents;
- finding unregistered torrents;
- finding pausedUP torrents;
- finding events about deleted torrents in qBittorrent API logs.

![demo](qpanel_demo.gif "qPanel demo")

## Getting Started

### Prerequisites

- Docker
- Docker Compose

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/liamniou/qpanel.git
    cd qpanel
    ```

2.  **Create a `.env` file** to specify the port you want to run the application on:
    ```
    FLASK_PORT=5000
    ```

3.  **Run the application:**
    ```bash
    docker compose up -d
    ```

4.  **Access the application** by navigating to `http://localhost:5000` in your web browser.

## Configuration

All configuration is done through the web interface. Simply navigate to the "Settings" page to configure global settings, and the "Instances" and "Rules" pages to manage your qBittorrent instances and rules. 
