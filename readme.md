# 🧭 TWS Trading BOT : P-729

A desktop trading application that connects to Interactive Brokers (TWS/IB Gateway), with a tkinter GUI and REST API for placing orders, managing positions, and automated stop-loss/take-profit in premarket and postmarket.


📚 **Table of Contents**
- [About](#-about)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Installation](#-installation)
- [Usage](#-usage)
- [Configuration](#-configuration)
- [Screenshots](#-screenshots)
- [API Documentation](#-api-documentation)
- [Contact](#-contact)


---

## 🧩 About

This project provides an intuitive interface for trading US stocks via Interactive Brokers. It combines a local tkinter desktop app with a Flask backend so you can place manual and conditional orders, use ATR-based or custom stop loss, take profit, and trade in premarket/postmarket with stop-limit orders. It supports multiple entry types (Custom, Limit Order, Conditional Order, FB, RB, RBB, PBe1, PBe2, LB, LB2, LB3) and can be built into a standalone Windows executable for distribution.


---

## ✨ Features

- **New Trade Frame** – Place manual and conditional orders with symbol, timeframe, risk, and entry type.
- **Manage Position** – View and manage open positions and order status.
- **Stop Loss & Take Profit** – ATR-based or custom stop loss; take profit ratios (1:1, 2:1, etc.); OCA (one-cancels-all) for TP/SL.
- **Premarket & Postmarket** – Extended-hours trading with stop-limit orders (limit = stop ± 3×stop_size).
- **REST API** – Flask API for health, auth, account balance, PnL, open/closed positions, and buying power (for web or mobile frontends).
- **Build to EXE** – PyInstaller/cx_Freeze support to create a single Windows executable (~50–80 MB).


---

## 🧠 Tech Stack

| Category    | Technologies |
|------------|--------------|
| **Languages** | Python 3.6+ |
| **Frameworks** | tkinter (GUI), Flask (REST API), ib_insync (IB API) |
| **Database** | SQLite (Flask-SQLAlchemy) for user/auth data |
| **Libraries** | numpy, pandas, TA-Lib, nest_asyncio, Flask-CORS, PyJWT |
| **Tools** | PyInstaller / cx_Freeze (build), TWS or IB Gateway |


---

## ⚙️ Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/tws-trading-gui.git

# Navigate to the project directory
cd tws-trading-gui

# Create a virtual environment (recommended)
python -m venv venv
venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Install TA-Lib (Windows: use the included wheel)
pip install TA_Lib-0.4.19-cp36-cp36m-win_amd64.whl

# Optional: install PyInstaller for building EXE
pip install pyinstaller
```

**Prerequisites**
- Python 3.6, 3.7, or 3.8
- Interactive Brokers account
- TWS or IB Gateway installed and running (e.g. on `127.0.0.1:7497` for TWS paper, or `7496` for live)


---

## 🚀 Usage

**Run the application (development):**

```bash
python app.py
```

Then:
- The tkinter window opens (New Trade / Manage Position).
- Flask API runs at **http://localhost:5000** (see [API Documentation](#-api-documentation)).

**Build a standalone EXE:**

```bash
build_pyinstaller.bat
```

The executable will be at: `dist\TWS_Trading_GUI.exe`


---

## 🧾 Configuration

**Config.py** (main app settings):

- `host`, `port`, `clientId` – TWS/IB Gateway connection (default `127.0.0.1`, `7497`, `99`).
- `tradingTime`, `outsideRthTradingtime` – Session start times.
- `atrPeriod`, `atrValue` – ATR settings for stop loss.
- `entryTradeType`, `stopLoss`, `takeProfit` – Entry and risk options.

**Environment variables (optional, for API):**

Create a `.env` file if you use one:

```env
SECRET_KEY=your_secret_key_here
DATABASE_URL=sqlite:///path/to/bot_users.db
```

**API CORS** – Allowed origins are set in `app.py` (e.g. `https://www.stocktrademanagement.com`, `http://localhost:3000`). Adjust as needed for your frontend.


---

## 🖼 Screenshots

_Add demo images or UI previews here._

| New Trade Frame | Manage Position |
|-----------------|-----------------|
| _(screenshot)_  | _(screenshot)_  |


---

## 📜 API Documentation

Base URL: `http://localhost:5000` (when running `python app.py`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check; no auth. |
| POST | `/api/auth/signup` | Register a new user. |
| POST | `/api/auth/login` | Login; returns JWT. |
| GET | `/api/auth/verify` | Verify JWT; returns user. |
| GET | `/api/account/balance` | Account balance (requires auth). |
| GET | `/api/account/daily-profit` | Daily PnL (requires auth). |
| GET | `/api/account/open-positions` | Open positions (requires auth). |
| GET | `/api/account/closed-positions` | Closed positions (requires auth). |
| GET | `/api/account/positions` | All positions (requires auth). |
| GET | `/api/account/buying-power` | Buying power (requires auth). |
| GET | `/api/account/summary` | Account summary (requires auth). |

Protected routes require the JWT in the `Authorization` header: `Bearer <token>`.


---

## 📬 Contact

- **Email:** 100terry001@gmail.com 
- **GitHub:** https://github.com/techteam92/IBKR_TradingBot 
- **Whatsapp:** +1 (343) 512-7592  


---

## 🌟 Acknowledgements

- **ib_insync** – Async Interactive Brokers API wrapper.
- **TA-Lib** – Technical analysis library.
- **Interactive Brokers** – TWS/IB Gateway and API.
- Inspiration and resources used in the project.

