from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "Server is Running"

def run():
    # Render จะใช้ Port 8080 หรือ Port ที่ระบบกำหนดให้
    app.run(host='0.0.0.0', port=8080)

def server_on():
    t = Thread(target=run)
    t.start()