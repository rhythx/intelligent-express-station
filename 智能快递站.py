# -*- coding: utf-8 -*-

# --- 导入所需库 ---
import paho.mqtt.client as mqtt  # 用于MQTT通信
import sqlite3                   # 用于数据库操作
import json                      # 用于处理JSON格式数据
import threading                 # 用于多线程，让各个服务同时运行
import webbrowser                # 用于自动打开浏览器
import http.server               # 用于创建HTTP Web服务器
import socketserver              # Web服务器框架
import os                        # 用于处理文件和目录路径
import asyncio                   # 用于异步编程 (WebSocket服务器)
import websockets                # 用于创建WebSocket服务器
import queue                     # 用于创建线程安全的队列，实现跨线程通信
from threading import Thread     # 明确导入Thread类
from urllib.parse import urlparse, parse_qs # 用于解析URL请求
from functools import partial    # 用于创建偏函数 (为HTTP处理器传递参数)
from datetime import datetime

# --- JSON数据格式说明 ---
"""
ESP32发布信息 (入库操作)
{
    "operation": 0,          # 操作码 0: 表示存储数据
    "name": "xiaoming",      # 收件人姓名
    "express_number": "SF12345678",      # 快递单号 (应唯一)
    "location_code": "1001", # 货物存放位置 (货架号)
    "phone_number": "18598777687"  # 收件人手机号
}

ESP32获取信息 (取件操作)
{
    "operation": 1,          # 操作码 1: 表示获取数据
    "phone_tail": "7687"     # 根据手机尾号查询
}

服务器->ESP32 (系统通知)
{
    "operation": 2,           # 操作码 2: 表示系统通知
    "reason": "not_found"     # 原因: "not_found" 表示手机尾号未找到
}
服务器 -> ESP32 (触发AI对话)
{
    "operation": 3,              # 操作码 3: 表示触发AI对话
    "event_description": "..."   # 事件描述，用于生成AI回复的Prompt
}
"""

# --- 全局配置 ---
# MQTT代理服务器设置
BROKER = "test.mosquitto.org"    # 公共测试用的MQTT服务器地址
PORT = 1883                      # MQTT标准端口
TOPIC_TX = "topic/esp32_tx"      # 接收来自ESP32数据的主题
TOPIC_RX = "topic/esp32_rx"      # 向ESP32发送数据的主题
TOPIC_STATUS = "topic/system_status" # 用于发布系统状态的主题 (例如：在线/离线)

# Web服务器设置
WEB_PORT = 8888                  # HTTP服务器端口
WEBSOCKET_PORT = 8889            # WebSocket服务器端口
# __file__ 是当前脚本的路径, os.path.abspath获取绝对路径, os.path.dirname获取目录
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webui')

# 数据库设置
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'express.db')

# --- 全局变量 ---
# 创建一个线程安全的队列，用于从MQTT/HTTP线程向WebSocket线程发送通知
notification_queue = queue.Queue()
# 创建一个集合，用于存放所有连接成功的WebSocket客户端
connected_clients = set()
# 创建一个线程局部存储对象，确保每个线程使用自己独立的数据库连接，避免冲突
thread_local = threading.local()

# --- 新增：通用数据清洗函数 (根源解决方案) ---
def clean_string(input_str):
    """
    清洗字符串，移除所有首尾的空白字符，包括空格、回车、换行等。
    如果输入不是字符串，则原样返回。
    """
    if isinstance(input_str, str):
        return input_str.strip()
    return input_str

# --- WebSocket 相关函数 ---
async def notify_clients():
    """向所有连接的WebSocket客户端发送'update'更新通知"""
    if connected_clients:
        message = "update"
        tasks = [client.send(message) for client in connected_clients]
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"已向 {len(connected_clients)} 个客户端发送 'update' 通知。")

async def process_notifications():
    """(在后台持续运行) 检查通知队列，并触发WebSocket消息发送"""
    while True:
        try:
            notification = notification_queue.get_nowait()
            if notification == "update":
                await notify_clients()
        except queue.Empty:
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"通知处理程序出错: {e}")

async def websocket_handler(websocket):
    """处理每一个新的WebSocket连接"""
    print("WebSocket 客户端已连接。")
    connected_clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        print("WebSocket 客户端已断开。")
        connected_clients.remove(websocket)

async def start_websocket_server_async():
    """异步启动WebSocket服务器和通知处理器"""
    asyncio.create_task(process_notifications())
    # 确保监听地址是 "0.0.0.0"
    async with websockets.serve(websocket_handler, "0.0.0.0", WEBSOCKET_PORT):
        # 为了清晰，打印信息也反映真实监听地址，虽然访问时仍用localhost
        print(f"WebSocket 服务器已启动于 ws://0.0.0.0:{WEBSOCKET_PORT}/ (浏览器请访问 ws://localhost:{WEBSOCKET_PORT})")
        await asyncio.Future()

def start_websocket_server():
    """在新的事件循环中运行异步的WebSocket服务器（供线程调用）"""
    asyncio.run(start_websocket_server_async())


# --- 数据库相关函数 ---
def get_db_connection():
    """获取一个当前线程专属的数据库连接"""
    if not hasattr(thread_local, 'conn'):
        thread_local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return thread_local.conn

def get_db_cursor():
    """获取当前线程数据库连接的游标"""
    return get_db_connection().cursor()

def init_db():
    """初始化数据库，如果表不存在则创建它"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS express_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            express_number TEXT UNIQUE,
            location_code TEXT,
            phone_number TEXT
        )
    ''')
     # [新增] 创建历史记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS express_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            express_number TEXT,
            location_code TEXT,
            phone_number TEXT,
            retrieval_time DATETIME
        )
    ''')
    conn.commit()
    conn.close()
    print("数据库初始化成功。")


# --- MQTT 回调函数 ---
def on_connect(client, userdata, flags, rc, properties=None):
    """当客户端连接到MQTT代理时的回调函数"""
    if rc == 0:
        print(f"成功连接到MQTT代理，返回码: {rc} (Success)")
        client.subscribe(TOPIC_TX, qos=1)
        print(f"已订阅主题: {TOPIC_TX}")
        try:
            status_payload = {"status": "connected"}
            client.publish(TOPIC_STATUS, json.dumps(status_payload), qos=1, retain=True)
            print(f"已发布连接状态到主题 '{TOPIC_STATUS}'")
        except Exception as e:
            print(f"发布连接状态失败: {e}")
    else:
        print(f"连接MQTT失败，返回码 {rc}: {mqtt.connack_string(rc)}")

def on_message(client, userdata, msg):
    """当从MQTT订阅的主题收到消息时的回调函数"""
    print(f"从主题 {msg.topic} 收到消息")
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        operation = payload.get('operation')
        
        # 入库操作 (operation == 0)，保持不变
        if operation == 0:
            name = clean_string(payload.get('name'))
            express_number = clean_string(payload.get('express_number'))
            location_code = clean_string(payload.get('location_code'))
            phone_number = clean_string(payload.get('phone_number'))
            
            if all([name, express_number, location_code, phone_number]):
                cursor = get_db_cursor()
                cursor.execute(
                    """INSERT INTO express_info (name, express_number, location_code, phone_number) 
                       VALUES (?, ?, ?, ?) 
                       ON CONFLICT(express_number) DO NOTHING""",
                    (name, express_number, location_code, phone_number)
                )
                get_db_connection().commit()
                if cursor.rowcount > 0:
                    print(f"数据插入成功: {payload}")
                    notification_queue.put_nowait("update")
                else:
                    print(f"忽略插入: 快递单号 '{express_number}' 已存在。")
            else:
                print("入库操作缺少必要字段。")
                
        # [已修正] 取件查询 (operation == 1)
        elif operation == 1:
            phone_tail = clean_string(payload.get('phone_tail'))
            if phone_tail and len(phone_tail) == 4 and phone_tail.isdigit():
                cursor = get_db_cursor()
                cursor.execute(
                    "SELECT * FROM express_info WHERE phone_number LIKE ?", 
                    ('%' + phone_tail,)
                )
                rows = cursor.fetchall()

                # 情况一：未找到匹配的包裹
                if not rows:
                    print(f"未找到手机尾号为 {phone_tail} 的记录，触发AI对话。")
                    response_payload = {
                        'operation': 3, 
                        'event_description': 'phone tail number not found'
                    }
                    client.publish(TOPIC_RX, json.dumps(response_payload), qos=1)
                
                # 情况二：找到了包裹
                else:
                    for row in rows:
                        # 1. 发送取件信息给ESP32
                        response_payload = {
                            'operation': 1, 'name': row[1], 'express_number': row[2],
                            'location_code': row[3], 'phone_number': row[4]
                        }
                        client.publish(TOPIC_RX, json.dumps(response_payload), qos=1)
                        print(f"已发送取件数据到 '{TOPIC_RX}': {response_payload}")

                        # 2. 将记录移动到历史表
                        try:
                            cursor.execute(
                                """INSERT INTO express_history (name, express_number, location_code, phone_number, retrieval_time)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (row[1], row[2], row[3], row[4], datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                            )
                            cursor.execute("DELETE FROM express_info WHERE id = ?", (row[0],))
                            get_db_connection().commit()
                            print(f"记录 ID:{row[0]} 已成功移至历史记录。")

                            # 3. 通知Web前端更新
                            notification_queue.put_nowait("update")

                        except Exception as e:
                            print(f"移动记录到历史表时出错: {e}")
                            get_db_connection().rollback()

                        # 4. 触发AI对话
                        success_payload = {
                            'operation': 3,
                            'event_description': f'retrieval success for user {row[1]}'
                        }
                        client.publish(TOPIC_RX, json.dumps(success_payload), qos=1)
            else:
                print(f"收到的手机尾号 '{phone_tail}' 格式不正确。")

    except Exception as e:
        print(f"on_message函数中发生严重错误: {e}")


# --- HTTP 请求处理器 ---
class ExpressRequestHandler(http.server.SimpleHTTPRequestHandler):
    """自定义HTTP请求处理器，用于处理API请求和提供静态文件"""
    def do_GET(self):
        # [修改] 增加对历史记录API的处理
        if self.path.startswith('/api/express/history'):
            cursor = get_db_cursor()
            cursor.execute("SELECT * FROM express_history ORDER BY retrieval_time DESC")
            rows = cursor.fetchall()
            history_data = [{'id': r[0], 'name': r[1], 'express_number': r[2], 'location_code': r[3], 'phone_number': r[4], 'retrieval_time': r[5]} for r in rows]
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(history_data).encode('utf-8'))
            return

        if self.path.startswith('/api/express'):
            query = parse_qs(urlparse(self.path).query)
            phone_tail = clean_string(query.get('phone_tail', [''])[0]) # 清洗查询参数
            
            cursor = get_db_cursor()
            # --- MODIFIED: Web端使用精确查询，因为数据源已保证干净 ---
            if phone_tail and len(phone_tail) == 4 and phone_tail.isdigit():
                # 使用 substr(TRIM(...)) 进行精确尾号匹配
                cursor.execute(
                    "SELECT * FROM express_info WHERE substr(TRIM(phone_number), -4) = ? ORDER BY id DESC", 
                    (phone_tail,)
                )
            else:
                # 如果没有提供phone_tail或格式不正确，则返回所有记录
                cursor.execute("SELECT * FROM express_info ORDER BY id DESC")
            
            rows = cursor.fetchall()
            express_data = [{'id': r[0], 'name': r[1], 'express_number': r[2], 'location_code': r[3], 'phone_number': r[4]} for r in rows]
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(express_data).encode('utf-8'))
            return
        
        super().do_GET()

    def do_POST(self):
        if self.path == '/api/express':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                # --- MODIFIED: 在获取数据后，立即使用 clean_string 函数进行清洗 ---
                name = clean_string(data.get('name'))
                express_number = clean_string(data.get('express_number'))
                location_code = clean_string(data.get('location_code'))
                phone_number = clean_string(data.get('phone_number'))
                
                if all([name, express_number, location_code, phone_number]):
                    cursor = get_db_cursor()
                    cursor.execute(
                        "INSERT INTO express_info (name, express_number, location_code, phone_number) VALUES (?, ?, ?, ?)",
                        (name, express_number, location_code, phone_number)
                    )
                    get_db_connection().commit()
                    notification_queue.put_nowait("update")
                    
                    self.send_response(201)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'success', 'message': '数据已插入'}).encode())
                else:
                    raise ValueError("缺少必要字段")
            except sqlite3.IntegrityError:
                 self.send_response(409)
                 self.send_header('Content-type', 'application/json')
                 self.send_header('Access-Control-Allow-Origin', '*')
                 self.end_headers()
                 self.wfile.write(json.dumps({'error': f'快递单号 {data.get("express_number")} 已存在'}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return
        
        super().do_POST()

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-Type")
        self.end_headers()


# --- 服务器启动函数 ---
def start_web_server(ready_event):
    """在独立线程中启动HTTP Web服务器"""
    Handler = partial(ExpressRequestHandler, directory=WEB_DIR)
    
    with socketserver.TCPServer(("", WEB_PORT), Handler) as httpd:
        print(f"HTTP 服务器已启动于 http://localhost:{WEB_PORT}/")
        ready_event.set()
        httpd.serve_forever()


# --- 主程序入口 ---
if __name__ == "__main__":
    # 1. 初始化数据库
    init_db()

    # 提醒用户一次性清理旧数据
    print("---" * 10)
    print("注意：如果数据库中存在旧的'脏数据'，建议手动清理一次。")
    print("可以使用DB Browser for SQLite等工具执行以下SQL命令：")
    print("UPDATE express_info SET phone_number = TRIM(replace(replace(phone_number, char(13), ''), char(10), ''));")
    print("---" * 10)

    # 2. 创建一个事件对象，用于同步Web服务器的启动
    server_ready_event = threading.Event()

    # 3. 配置并启动MQTT客户端
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.loop_start()

    # 4. 在一个守护线程中启动Web服务器
    web_thread = Thread(target=start_web_server, args=(server_ready_event,), daemon=True)
    web_thread.start()

    # 5. 在另一个守护线程中启动WebSocket服务器
    websocket_thread = Thread(target=start_websocket_server, daemon=True)
    websocket_thread.start()

    # 6. 等待Web服务器完全就绪
    print("主线程: 等待Web服务器准备就绪...")
    server_ready_event.wait() 
    print("主线程: Web服务器已就绪！现在打开浏览器。")
    
    # 7. 自动打开浏览器到管理页面
    webbrowser.open(f'http://localhost:{WEB_PORT}/index.html')

    try:
        # 8. 让主线程保持运行，以维持整个程序的活动
        while True:
            pass
    except KeyboardInterrupt:
        # 9. 当用户按下Ctrl+C时，执行优雅的关闭流程
        print("\n正在退出程序...")
        client.loop_stop()
        # 确保数据库连接被关闭
        db_conn = getattr(thread_local, 'conn', None)
        if db_conn:
            db_conn.close()
        print("系统已平稳关闭。")