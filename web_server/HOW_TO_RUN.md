# How to Run the Applications

## Two Separate Applications

This project contains **two independent applications**:

### 1. Trading GUI Application (Root Directory)
- **File**: `app.py` (in root directory)
- **Type**: Tkinter desktop application
- **Purpose**: Trading interface connected to Interactive Brokers
- **How to run**:
  ```bash
  # From project root
  python app.py
  ```

### 2. Web Server Application (web_server directory)
- **File**: `web_server/app.py`
- **Type**: Flask web server
- **Purpose**: Backend API for bot management website (user authentication)
- **How to run** (choose one method):

  **Method 1: Direct**
  ```bash
  cd web_server
  python app.py
  ```

  **Method 2: Using run_server.py**
  ```bash
  cd web_server
  python run_server.py
  ```

  **Method 3: Using batch file (Windows)**
  ```bash
  cd web_server
  run_server.bat
  ```

## Running Both Applications

You can run both applications simultaneously if needed:
- The Trading GUI runs independently
- The Web Server runs on `http://localhost:5000`

They do not interfere with each other.

## Which One Do I Need?

- **Trading GUI**: Use this if you want to trade directly from the desktop application
- **Web Server**: Use this if you have a React.js frontend website that needs to connect to the API for user management
