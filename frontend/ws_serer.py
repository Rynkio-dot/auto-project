# -*- coding: UTF-8 -*-
"""
酒店机器人控制台

Windows 当 WebSocket 服务端，点房间号/返航/充电/停止，把点位发给小车。
小车端还是 robot/ws_client.py（actionlib 让 move_base 导航），本文件不用改小车端。

运行：python ws_serer.py
"""
import json
import sys

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QLabel, QPushButton,
                             QTextEdit, QGroupBox)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import QObject, pyqtSignal, Qt
from PyQt5.QtNetwork import QHostAddress
from PyQt5.QtWebSockets import QWebSocketServer

# ========== 点位配置（保持同步） ==========
# 房间号 -> 点位号（界面按钮按这里生成）
ROOM_POINTS = [
    ("101", 101),
    ("102", 102),
    ("103", 103),
]

# 点位坐标：自己地图 map 坐标系，单位 米 / 弧度（先占位，量好后替换）
NAV_POINTS = {
    101: {"x": 3.43, "y": -0.494, "yaw": 0.00581},
    102: {"x": 3.76, "y": -2.1, "yaw": 0.00313},
    103: {"x": 2.68, "y": -2.35, "yaw": 0.00463},
}

# 返航点 / 充电桩（先占位，量好后替换）
HOME_POINT = {"x": 1.97, "y": -0.62, "yaw": 0.00375}
# CHARGE_POINT = {"x": 0.0, "y": 0.0, "yaw": 0.0} 不设充电点

# 点位号 -> 显示名（收到小车状态时用）
POINT_NAMES = {101: "客房101", 102: "客房102", 103: "客房103",
               "home": "返航点", "charge": "充电桩"}

# ========== WebSocket 服务端（基础技术不变） ==========
class CarWebSocketServer(QObject):
    """管理小车连接、收发 JSON 消息"""

    car_connected = pyqtSignal(str)        # 小车地址
    car_disconnected = pyqtSignal()
    message_received = pyqtSignal(dict)    # 小车发来的 JSON

    def __init__(self, port=8765, parent=None):
        super(CarWebSocketServer, self).__init__(parent)
        self.port = port
        self.clients = []

        self.server = QWebSocketServer("HotelCarServer", QWebSocketServer.NonSecureMode, self)
        self.server.newConnection.connect(self.on_new_connection)

    def start(self):
        return self.server.listen(QHostAddress.AnyIPv4, self.port)

    def error_text(self):
        return self.server.errorString()

    def close(self):
        for socket in self.clients:
            socket.close()
        self.clients = []
        self.server.close()

    def is_connected(self):
        return len(self.clients) > 0

    def on_new_connection(self):
        socket = self.server.nextPendingConnection()
        if socket is None:
            return
        self.clients.append(socket)
        socket.textMessageReceived.connect(
            lambda text, s=socket: self.on_text_message(s, text))
        socket.disconnected.connect(lambda s=socket: self.on_disconnected(s))
        address = "{}:{}".format(socket.peerAddress().toString(), socket.peerPort())
        self.car_connected.emit(address)

    def on_disconnected(self, socket):
        if socket in self.clients:
            self.clients.remove(socket)
        socket.deleteLater()
        if not self.clients:
            self.car_disconnected.emit()

    def on_text_message(self, socket, text):
        try:
            data = json.loads(text)
        except ValueError:
            return
        if data.get("type") == "hello":
            self.send_to(socket, {"type": "hello_ack", "server": "hotel_console"})
        self.message_received.emit(data)

    def send_data(self, data):
        count = 0
        for socket in list(self.clients):
            if self.send_to(socket, data):
                count += 1
        return count

    def send_to(self, socket, data):
        try:
            socket.sendTextMessage(json.dumps(data, ensure_ascii=False))
            return True
        except Exception:
            return False


# ========== 主窗口 ==========
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("酒店机器人控制台")
        self.resize(560, 620)
        self.setStyleSheet("""
            QMainWindow{background-color:#E6F2FF;}
            QGroupBox{
                font-size:15px;font-weight:bold;border:1px solid #99C8F0;
                border-radius:8px;margin-top:10px;padding-top:14px;background:#F0F8FF;
            }
            QLabel{font-size:14px;}
            QPushButton{
                padding:8px 12px;font-size:16px;font-weight:bold;
                border-radius:8px;border:none;background-color:#73B8F5;color:#002b80;
            }
            QPushButton:hover{background-color:#429CE6;color:white;}
            QTextEdit{border:1px solid #88B8E8;border-radius:6px;background:white;font-size:13px;}
        """)

        self.car_server = CarWebSocketServer(port=8765)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        # 标题 + 连接状态
        title = QLabel("酒店服务机器人")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        layout.addWidget(title)

        self.link_label = QLabel("小车未连接!  请先在小车上运行 ws_client.py")
        self.link_label.setStyleSheet("color:#d32f2f;")
        layout.addWidget(self.link_label)

        # 房间按钮
        room_group = QGroupBox("房间导航（点一下小车自动前往）")
        room_layout = QGridLayout(room_group)
        room_layout.setSpacing(12)
        for index, (room, point) in enumerate(ROOM_POINTS):
            info = NAV_POINTS[point]
            btn = QPushButton(room)
            btn.setMinimumHeight(64)
            btn.setToolTip("点位 {}\nx={}, y={}, yaw={}".format(
                point, info["x"], info["y"], info["yaw"]))
            btn.clicked.connect(lambda checked=False, p=point, n=room: self.send_room(p, n))
            room_layout.addWidget(btn, index // 2, index % 2)
        layout.addWidget(room_group)

        # 返航 / 充电 / 停止
        op_group = QGroupBox("运维")
        op_layout = QHBoxLayout(op_group)
        self.btn_home = QPushButton("返航")
        self.btn_charge = QPushButton("充电")
        self.btn_stop = QPushButton("停止")
        for btn in [self.btn_home, self.btn_charge, self.btn_stop]:
            btn.setMinimumHeight(48)
            op_layout.addWidget(btn)
        layout.addWidget(op_group)

        # 状态
        self.status_label = QLabel("当前状态：空闲")
        self.status_label.setFont(QFont("Microsoft YaHei", 15, QFont.Bold))
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        # 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(150)
        layout.addWidget(self.log_text)

        # 绑定
        self.btn_home.clicked.connect(self.send_home)
        self.btn_charge.clicked.connect(self.send_home)
        self.btn_stop.clicked.connect(self.send_stop)
        self.car_server.car_connected.connect(self.on_car_connected)
        self.car_server.car_disconnected.connect(self.on_car_disconnected)
        self.car_server.message_received.connect(self.on_car_message)
        if self.car_server.start():
            self.log("【WebSocket】服务端已启动，端口 8765（小车端运行：python3 ws_client.py --host 本机IP）")
        else:
            self.log("【WebSocket】服务端启动失败：{}".format(self.car_server.error_text()))

    # ---------- 发导航 ----------
    def send_nav(self, point, name, pose):
        """统一发导航命令：point 是点位号，name 是显示名，pose 是 x/y/yaw"""
        if not self.car_server.is_connected():
            self.link_label.setText("小车未连接!")
            self.link_label.setStyleSheet("color:#d32f2f;")
            self.log("【导航】{} 发送失败：小车未连接".format(name))
            return
        count = self.car_server.send_data({"type": "nav", "point": point,
                                           "x": pose["x"], "y": pose["y"],
                                           "yaw": pose["yaw"],
                                           "home": HOME_POINT})
        if count == 0:
            self.status_label.setText("当前状态：发送失败，小车可能已断开")
            self.log("【导航】{} 发送失败".format(name))
            return
        self.status_label.setText("当前状态：正在前往 {} …".format(name))
        self.log("【导航】下发 {}：x={}, y={}, yaw={}".format(
            name, pose["x"], pose["y"], pose["yaw"]))

    def send_room(self, point, room):
        self.send_nav(point, "客房" + room, NAV_POINTS[point])

    def send_home(self):
        self.send_nav("home", "返航点", HOME_POINT)

    # def send_charge(self):
    #     self.send_nav("charge", "充电桩", CHARGE_POINT)

    def send_stop(self):
        if not self.car_server.is_connected():
            self.link_label.setText("小车未连接!")
            self.link_label.setStyleSheet("color:#d32f2f;")
            return
        self.car_server.send_data({"type": "stop"})
        self.status_label.setText("当前状态：已停止")
        self.log("【导航】下发停止命令")

    # ---------- 小车消息 ----------
    def on_car_connected(self, address):
        self.link_label.setText("小车已连接{}".format(address))
        self.link_label.setStyleSheet("color:#2e7d32;")
        self.log("【WebSocket】小车已连接：{}".format(address))

    def on_car_disconnected(self):
        self.link_label.setText("小车未连接!")
        self.link_label.setStyleSheet("color:#d32f2f;")
        self.status_label.setText("当前状态：小车已断开")
        self.log("【WebSocket】小车连接断开")

    def on_car_message(self, data):
        if data.get("type") != "status":
            return
        state = data.get("state", "")
        point = data.get("point", "")
        name = POINT_NAMES.get(point, point)
        if state == "moving":
            self.status_label.setText("当前状态：正在前往 {} …".format(name))
        elif state == "arrived":
            self.status_label.setText("当前状态：已到达 {}".format(name))
            self.log("【导航】小车已到达 {}".format(name))
        elif state == "failed":
            self.status_label.setText("当前状态：导航失败（{}）".format(data.get("message", "")))
            self.log("【导航】导航失败：{}".format(data.get("message", "")))
        elif state == "idle":
            self.status_label.setText("当前状态：空闲")
            self.log("【导航】小车已停止")

    # ---------- 日志 / 关闭 ----------
    def log(self, msg):
        self.log_text.append(msg)

    def closeEvent(self, event):
        self.car_server.close()
        super(MainWindow, self).closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
