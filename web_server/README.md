# Bot Management Web Server

Backend server for managing the trading bot remotely via a React.js website.

## Features

- User signup and registration
- User login with JWT authentication
- User profile management
- Password change functionality
- Secure password hashing
- SQLite database for user storage (can be upgraded to PostgreSQL)

## Setup Instructions

### 1. Install Dependencies

```bash
cd web_server
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the `web_server` directory with the following content:

```
SECRET_KEY=your-secret-key-change-this-in-production-use-a-random-string
DATABASE_URL=sqlite:///bot_users.db
```

**Important:** Change the `SECRET_KEY` to a strong random string for production. You can generate one using:
```python
import secrets
print(secrets.token_hex(32))
```

### 3. Run the Server

```bash
python app.py
```

The server will start on `http://localhost:5000`

## API Endpoints

### Public Endpoints

#### Health Check
- **GET** `/api/health`
- Returns server status

#### Signup
- **POST** `/api/signup`
- Body: `{ "username": "user123", "email": "user@example.com", "password": "password123" }`
- Returns: `{ "message": "User created successfully", "token": "...", "user": {...} }`

#### Login
- **POST** `/api/login`
- Body: `{ "username": "user123", "password": "password123" }`
- Returns: `{ "message": "Login successful", "token": "...", "user": {...} }`

### Protected Endpoints (Require JWT Token)

Include the token in the Authorization header: `Authorization: Bearer <token>`

#### Get Profile
- **GET** `/api/user/profile`
- Returns current user information

#### Update Profile
- **PUT** `/api/user/profile`
- Body: `{ "email": "newemail@example.com", "username": "newusername" }`
- Returns updated user information

#### Change Password
- **POST** `/api/user/change-password`
- Body: `{ "old_password": "oldpass", "new_password": "newpass" }`
- Returns success message

## React.js Integration

### Example: Signup Request

```javascript
const signup = async (username, email, password) => {
  const response = await fetch('http://localhost:5000/api/signup', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ username, email, password }),
  });
  
  const data = await response.json();
  if (response.ok) {
    // Store token in localStorage or state
    localStorage.setItem('token', data.token);
    return data;
  } else {
    throw new Error(data.error);
  }
};
```

### Example: Login Request

```javascript
const login = async (username, password) => {
  const response = await fetch('http://localhost:5000/api/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ username, password }),
  });
  
  const data = await response.json();
  if (response.ok) {
    localStorage.setItem('token', data.token);
    return data;
  } else {
    throw new Error(data.error);
  }
};
```

### Example: Authenticated Request

```javascript
const getProfile = async () => {
  const token = localStorage.getItem('token');
  const response = await fetch('http://localhost:5000/api/user/profile', {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  });
  
  const data = await response.json();
  return data;
};
```

## Database

The server uses SQLite by default (stored in `bot_users.db`). For production, consider using PostgreSQL:

1. Install PostgreSQL
2. Update `DATABASE_URL` in `.env`:
   ```
   DATABASE_URL=postgresql://username:password@localhost/bot_users
   ```
3. Install psycopg2: `pip install psycopg2-binary`

## Production Deployment

For production, use a proper WSGI server like Gunicorn:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## Security Notes

- Change the `SECRET_KEY` in production
- Use HTTPS in production
- Consider rate limiting for login/signup endpoints
- Add input validation and sanitization
- Use environment variables for sensitive configuration
