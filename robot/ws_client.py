# -*- coding: UTF-8 -*-
"""
这是小车端（Ubuntu arm64 + ROS Melodic）WebSocket 客户端

连接 Windows 上的 Qt 服务端，收到导航命令后让小车前往对应导航点，
到达后自动返回原点（HOME_POINT），导航结果再通过 WebSocket 发回 Windows。

运行前先在小车上启动导航（两个终端）：
    roslaunch transbot_bringup transbot_robot.launch
    roslaunch transbot_navigation transbot_navigation.launch
然后运行本脚本：
    python3 ws_client.py --host 192.168.1.100
依赖：pip3 install websocket-client
"""
import argparse
import json
import math
import threading
import time

import rospy
import websocket       # pip3 install websocket-client
from actionlib import SimpleActionClient
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal

GOAL_SUCCEEDED = 3

# 原点（HOME_POINT）：到目标点后自动回到这里
# Windows 端每次发导航命令都会带上 "home" 坐标，以 Windows 那边的 HOME_POINT 为准；
# 这里只是没收到 "home" 时的默认值，可以按自己地图改
HOME_POINT = {"x": 1.97, "y": -0.62, "yaw": 0.00375}

# 这两个点本身就不用再回原点了：返航点就是原点，充电桩要停着充电
NO_RETURN_POINTS = ("home", "charge")


class NavController(object):
    def __init__(self, status_cb=None):
        self.status_cb = status_cb      # 导航状态回调，参数是 dict
        self.client = SimpleActionClient("move_base", MoveBaseAction)
        self.thread = None
        self.current = None       # 当前目标标记，用来丢弃旧目标的结果
        self.home = dict(HOME_POINT)    # 当前原点，Windows 下发的会比这个优先

    def wait_server(self, timeout=60):
        """等待 move_base 启动，最多等 timeout 秒"""
        rospy.loginfo("等待 move_base 服务 ...")
        return self.client.wait_for_server(rospy.Duration(timeout))

    def goto(self, x, y, yaw, go_home=False):
        """导航到 (x, y, yaw)，到了以后 go_home=True 就再回原点，不阻塞"""
        token = object()
        self.current = token
        if self.thread is not None and self.thread.is_alive():
            self.client.cancel_all_goals()      # 如果上一个点还没走完，先取消
        self.thread = threading.Thread(target=self._goto_thread,
                                       args=(x, y, yaw, token, go_home))
        self.thread.daemon = True
        self.thread.start()

    def _goto_thread(self, x, y, yaw, token, go_home):
        if not self._drive(x, y, yaw, token, "导航失败，状态码 {}"):
            return
        self._report("arrived", "已到达")
        if not go_home:
            return
        # 到目标点了，再回原点
        home = self.home
        rospy.loginfo("返回原点: x=%.2f, y=%.2f, yaw=%.3f",
                      home["x"], home["y"], home["yaw"])
        self._report("moving", "正在返回原点", point="home")
        if not self._drive(home["x"], home["y"], home["yaw"], token,
                           "返回原点失败，状态码 {}"):
            return
        self._report("arrived", "已回到原点", point="home")

    def _drive(self, x, y, yaw, token, fail_msg):
        """发一个目标点并等结果，成功返回 True"""
        goal = MoveBaseGoal()
        goal.target_pose.header.frame_id = "map"
        goal.target_pose.header.stamp = rospy.Time.now()
        goal.target_pose.pose.position.x = x
        goal.target_pose.pose.position.y = y
        # 把 yaw（弧度）转成四元数，只需要 z 和 w 两个分量
        goal.target_pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal.target_pose.pose.orientation.w = math.cos(yaw / 2.0)

        rospy.loginfo("导航中: x=%.2f, y=%.2f, yaw=%.3f", x, y, yaw)
        self.client.send_goal(goal)
        self.client.wait_for_result()

        # 如果已经被新目标或停止命令顶掉，旧结果就不用再报给 Windows 了
        if self.current is not token:
            return False
        state = self.client.get_state()
        if state == GOAL_SUCCEEDED:
            return True
        self._report("failed", fail_msg.format(state))
        return False

    def cancel(self):
        """取消当前导航"""
        self.current = None
        self.client.cancel_all_goals()

    def _report(self, state, message, point=None):
        rospy.loginfo("导航结果：%s", message)
        if self.status_cb is not None:
            self.status_cb({"state": state, "message": message, "point": point})



class CarClient(object):
    def __init__(self, host, port):
        self.url = "ws://{}:{}/".format(host, port)
        self.ws = None
        self.point = None       # 当前导航点号，例如 101
        self.nav = None         # 导航控制器，等 rospy.init_node 之后再创建

    # ---------- 把导航状态发回 Windows ----------
    def on_nav_status(self, status):
        self.send_json({"type": "status",
                        "point": status.get("point") or self.point,
                        "state": status["state"],
                        "message": status.get("message", "")})

    def send_json(self, data):
        if self.ws is None:
            return
        try:
            self.ws.send(json.dumps(data))
        except Exception as e:
            print("发送失败：{}".format(e))

    # ---------- 处理 Windows 发来的消息 ----------
    def handle_message(self, text):
        try:
            data = json.loads(text)
        except ValueError:
            return
        msg_type = data.get("type", "")
        if msg_type == "nav":
            self.point = data.get("point")
            x = float(data.get("x", 0.0))
            y = float(data.get("y", 0.0))
            yaw = float(data.get("yaw", 0.0))
            # Windows 发来的原点坐标（home），到了目标点以后回这里
            home = data.get("home")
            if isinstance(home, dict):
                self.nav.home = {"x": float(home.get("x", 0.0)),
                                 "y": float(home.get("y", 0.0)),
                                 "yaw": float(home.get("yaw", 0.0))}
            go_home = self.point not in NO_RETURN_POINTS
            print("收到导航命令：点 {} -> x={}, y={}, yaw={}，到达后{}".format(
                self.point, x, y, yaw, "返回原点" if go_home else "不回原点"))
            self.send_json({"type": "status", "point": self.point,
                            "state": "moving", "message": "开始导航"})
            self.nav.goto(x, y, yaw, go_home=go_home)
        elif msg_type == "stop":
            print("收到停止命令，取消导航")
            self.nav.cancel()
            self.send_json({"type": "status", "point": self.point,
                            "state": "idle", "message": "已停止导航"})
        elif msg_type == "hello_ack":
            print("已连接到 Windows 服务端")

    # ---------- 主循环：连接、接收、断线重连 ----------
    def run(self):
        rospy.init_node("transbot_ws_client", anonymous=True)
        # 注意：要先 init_node，才能创建 action 客户端
        self.nav = NavController(status_cb=self.on_nav_status)
        if not self.nav.wait_server(60):
            print("move_base 没有启动！请先运行 transbot_navigation.launch")
            return
        while not rospy.is_shutdown():
            try:
                print("正在连接 {} ...".format(self.url))
                self.ws = websocket.create_connection(self.url, timeout=5)
                self.ws.settimeout(None)      # 连上以后一直阻塞等待消息
                self.send_json({"type": "hello", "role": "car", "name": "transbot"})
                while not rospy.is_shutdown():
                    text = self.ws.recv()
                    if not text:
                        break
                    self.handle_message(text)
                self.ws.close()
            except Exception as e:
                print("连接断开：{}，3 秒后重连".format(e))
            self.ws = None
            time.sleep(3)


def main():
    parser = argparse.ArgumentParser(description="Transbot 小车 WebSocket 客户端")
    parser.add_argument("--host", default="192.168.1.10", help="Windows 电脑的 IP")
    parser.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    args = parser.parse_args()
    CarClient(args.host, args.port).run()


if __name__ == "__main__":
    main()
