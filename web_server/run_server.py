"""
Simple script to run the Flask server
"""

from app import app, init_db

if __name__ == '__main__':
    # Initialize database
    print("Initializing database...")
    init_db()
    print("Database initialized!")
    
    # Run server
    print(f"Starting server on http://localhost:5000")
    print("Press Ctrl+C to stop the server")
    app.run(host='0.0.0.0', port=5000, debug=True)
