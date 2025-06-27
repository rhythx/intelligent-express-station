# --- START OF FILE 智能快递站.py ---

import paho.mqtt.client as mqtt
import sqlite3
import json
import threading
import webbrowser
import http.server
import socketserver
import os
from threading import Thread
from urllib.parse import urlparse, parse_qs
import asyncio
import websockets
import queue

"""
json格式
esp32发布信息
{
  "operation":0,          #操作数0为储存
  "name": "xiaoming",       #姓名
  "express_number":"0001",      #快递单号
  "location_code":"0101",      #货物位置码
  "phone_number":"18598777687"  #手机号
}
esp32获取信息
{
  "operation":1,            #操作数1为获取
  "phone_tail":"7687"       #手机尾号
}
"""

# MQTT Broker的设置
broker = "test.mosquitto.org"
port = 1883
topic_tx = "topic/esp32_tx"  # 接收数据的主题
topic_rx = "topic/esp32_rx"  # 发送数据的主题
topic_status = "topic/system_status" # 新增：用于发布系统状态的主题

# Web服务器设置
WEB_PORT = 8000
WEBSOCKET_PORT = 8001
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webui')

# WebSocket连接集合
connected_clients = set()
# 创建一个线程安全的队列用于跨线程通信
notification_queue = queue.Queue()


# --- WebSocket相关函数 ---
async def notify_clients():
    """向所有连接的WebSocket客户端发送更新通知"""
    if connected_clients:
        message = "update"
        tasks = [client.send(message) for client in connected_clients]
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"Sent 'update' notification to {len(connected_clients)} client(s).")

async def process_notifications():
    """从队列中获取通知并触发WebSocket消息发送"""
    while True:
        try:
            notification = notification_queue.get_nowait()
            if notification == "update":
                await notify_clients()
        except queue.Empty:
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error in notification processor: {e}")


async def websocket_handler(websocket):
    """处理新的WebSocket连接"""
    print("WebSocket client connected.")
    connected_clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        print("WebSocket client disconnected.")
        connected_clients.remove(websocket)


async def start_websocket_server_async():
    """异步启动WebSocket服务器和通知处理器"""
    # 直接创建并启动后台任务，无需赋值给变量
    asyncio.create_task(process_notifications())
    async with websockets.serve(websocket_handler, "localhost", WEBSOCKET_PORT):
        print(f"WebSocket server started at ws://localhost:{WEBSOCKET_PORT}/")
        await asyncio.Future()  # run forever


def start_websocket_server():
    """在新的事件循环中运行WebSocket服务器"""
    asyncio.run(start_websocket_server_async())


# --- 数据库设置 ---
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'express.db')
thread_local = threading.local()

def get_connection():
    if not hasattr(thread_local, 'conn'):
        thread_local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return thread_local.conn

def get_cursor():
    return get_connection().cursor()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS express_info (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        express_number TEXT,
                        location_code TEXT,
                        phone_number TEXT)''')
    conn.commit()
    conn.close()


# --- 回调函数和HTTP处理器 ---
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"Connected to MQTT Broker with result code {rc} (Success)")
        client.subscribe(topic_tx, qos=1)

        # --- 新增逻辑：发布连接成功状态消息 ---
        try:
            status_payload = {
                "status": "connected"
            }
            client.publish(topic_status, json.dumps(status_payload), qos=1, retain=True)
            print(f"Published connection status to topic '{topic_status}'")
        except Exception as e:
            print(f"Failed to publish connection status: {e}")
        # --- 新增逻辑结束 ---
    else:
        print(f"Failed to connect to MQTT, result code {rc}: {mqtt.connack_string(rc)}")


def on_message(client, userdata, msg):
    print(f"Received message on topic {msg.topic}")
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        operation = payload.get('operation')
        if operation == 0:
            name = payload.get('name')
            express_number = payload.get('express_number')
            location_code = payload.get('location_code')
            phone_number = payload.get('phone_number')
            if all([name, express_number, location_code, phone_number]):
                cursor = get_cursor()
                cursor.execute("INSERT INTO express_info (name, express_number, location_code, phone_number) VALUES (?,?,?,?)",
                               (name, express_number, location_code, phone_number))
                get_connection().commit()
                print(f"Data inserted into database: {payload}")
                notification_queue.put_nowait("update")
            else:
                print("Missing required fields in the payload for storage")
        elif operation == 1:
            phone_tail = payload.get('phone_tail')
            if phone_tail:
                cursor = get_cursor()
                cursor.execute("SELECT * FROM express_info WHERE phone_number LIKE?", ('%' + phone_tail,))
                rows = cursor.fetchall()
                for row in rows:
                    response_payload = {
                        'operation': 1, 'name': row[1], 'express_number': row[2],
                        'location_code': row[3], 'phone_number': row[4]
                    }
                    client.publish(topic_rx, json.dumps(response_payload), qos=1)
                    print(f"Message '{response_payload}' published to topic '{topic_rx}'")
    except Exception as e:
        print(f"An error occurred in on_message: {e}")

class ExpressRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        if self.path.startswith('/api/express'):
            query = parse_qs(urlparse(self.path).query)
            phone_tail = query.get('phone_tail', [''])[0]
            cursor = get_cursor()
            if phone_tail:
                cursor.execute("SELECT * FROM express_info WHERE phone_number LIKE ?", ('%' + phone_tail,))
            else:
                cursor.execute("SELECT * FROM express_info")
            rows = cursor.fetchall()
            express_data = [{'id': r[0], 'name': r[1], 'express_number': r[2], 'location_code': r[3], 'phone_number': r[4]} for r in rows]
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(express_data).encode())
            return
        super().do_GET()

    def do_POST(self):
        if self.path == '/api/express':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                name, express_number, location_code, phone_number = data.get('name'), data.get('express_number'), data.get('location_code'), data.get('phone_number')
                if all([name, express_number, location_code, phone_number]):
                    cursor = get_cursor()
                    cursor.execute("INSERT INTO express_info (name, express_number, location_code, phone_number) VALUES (?,?,?,?)",
                                   (name, express_number, location_code, phone_number))
                    get_connection().commit()
                    notification_queue.put_nowait("update")
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
                    self.wfile.write(json.dumps({'status': 'success'}).encode())
                else:
                    raise ValueError("Missing required fields")
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return
        super().do_POST()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

def start_web_server(ready_event): # <--- 接收一个 event 参数
    """启动Web服务器并在准备好后发出信号"""
    with socketserver.TCPServer(("localhost", WEB_PORT), ExpressRequestHandler) as httpd:
        print(f"HTTP server started at http://localhost:{WEB_PORT}/")
        # 服务器已经成功绑定端口并准备好接受请求
        # 现在，举起信号旗，通知主线程
        ready_event.set() 
        httpd.serve_forever()


# --- 主程序 ---
if __name__ == "__main__":
    init_db()

    # 创建一个Event对象，用于同步
    server_ready_event = threading.Event()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker, port, 60)
    client.loop_start()

    # 启动Web服务器线程，并将event对象作为参数传递
    web_thread = Thread(target=start_web_server, args=(server_ready_event,), daemon=True)
    web_thread.start()

    websocket_thread = Thread(target=start_websocket_server, daemon=True)
    websocket_thread.start() 
    
    # --- 替换掉旧的Timer逻辑 ---
    print("Main thread: Waiting for web server to be ready...")
    # 调用 event.wait()，主线程会在这里阻塞，直到web_thread调用了event.set()
    server_ready_event.wait() 
    print("Main thread: Web server is ready! Opening browser now.")
    
    # 一旦等待结束，说明服务器100%准备好了，立即打开浏览器
    webbrowser.open(f'http://localhost:{WEB_PORT}/index.html')

    try:
        # 主线程保持运行，等待Ctrl+C
        while True:
            pass
    except KeyboardInterrupt:
        print("\nExiting...")
        client.loop_stop()
        if hasattr(thread_local, 'conn'):
            thread_local.conn.close()