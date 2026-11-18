# 酒店机器人控制台（极简版）

只有两个文件：

| 文件 | 在哪运行 | 作用 |
| --- | --- | --- |
| `frontend/ws_serer.py` | Windows | 界面 + WebSocket 服务端 |
| `robot/ws_client.py` | 小车 Ubuntu | WebSocket 客户端 + 导航（到点后自动回原点） |

界面只有：**点位按钮（101/102/103）+ 返航 / 充电 / 停止 + 状态 + 日志**。

## 一、Windows 上启动

```bash
pip install PyQt5
cd frontend
python ws_serer.py
```

- 点 101 / 102 / 103：小车去对应点位，**到了以后自动回到原点（HOME_POINT）**
- 点「返航」或「充电」：都回原点（现在两个键是同一个动作）
- 点「停止」：让小车停下

## 二、小车上启动（Ubuntu arm64 + ROS Melodic）

```bash
# 终端 1：底层
roslaunch transbot_bringup transbot_robot.launch

# 终端 2：导航（换成自己建好的地图）
roslaunch transbot_navigation transbot_navigation.launch map_file:=$HOME/my_map.yaml

# 终端 3：WebSocket 客户端（换成 Windows 的 IP）
pip3 install websocket-client
python3 ws_client.py --host 192.168.1.100
```

RViz 里先用「2D Pose Estimate」设一次初始位姿（不设导航会失败）。
两台机器要在同一局域网（Windows 开热点让小车连即可），小车不用上互联网。

## 三、点位配置

打开 `frontend/ws_serer.py`，最上面：

```python
# 点位号（界面按钮名字）-> 点位号
ROOM_POINTS = [("101", 101), ("102", 102), ("103", 103)]

# 点位坐标（地图 map 坐标系，单位：米 / 弧度）
NAV_POINTS = {
    101: {"x": 3.43, "y": -0.494, "yaw": 0.00581},
    102: {"x": 3.76, "y": -2.1,  "yaw": 0.00313},
    103: {"x": 2.68, "y": -2.35, "yaw": 0.00463},
}

# 原点：到点位后自动回到这里；点「返航 / 充电」也是回这里
HOME_POINT = {"x": 1.97, "y": -0.62, "yaw": 0.00375}
```

- 加点：`ROOM_POINTS` 加一行 + `NAV_POINTS` 加坐标，按钮自动多出来
- 坐标在 RViz 里用 **Publish Point** 量（`yaw` 拿不准先填 0）
- `HOME_POINT` 只在前端配一份，Windows 每次发导航命令会把它一起发给小车，**不用在小车上再改一次**

## 四、到点自动回原点（小车端行为）

`robot/ws_client.py` 收到 `nav` 命令后：

1. 先去目标点，到了回报 `state=arrived`；
2. 接着自动导航回 `home`（Windows 下发的 `HOME_POINT`），回报：
   - `{"type":"status","point":"home","state":"moving","message":"正在返回原点"}`
   - `{"type":"status","point":"home","state":"arrived","message":"已回到原点"}`
3. 例外：`point` 是 `home`（返航/充电本身）时不再重复回原点；导航失败也不回。

界面会依次显示「已到达 客房101 ✅」→「正在前往 返航点 …」→「已到达 返航点 ✅」。

## 五、通信协议

- Windows → 小车：`{"type":"nav","point":101,"x":3.43,"y":-0.494,"yaw":0.00581,"home":{"x":1.97,"y":-0.62,"yaw":0.00375}}`、`{"type":"stop"}`
- 小车 → Windows：`{"type":"status","point":101,"state":"moving|arrived|failed|idle","message":"..."}`（返回原点时 `point` 为 `"home"`）

`robot/ws_client.py` 用 actionlib 给 move_base 发目标点，等价于 RViz 里的「2D Nav Goal」。

> Git 同步 / clone / push 说明：[docs/内容书.md](docs/内容书.md)
> 每轮修改记录见 `docs/开发日志.md`；仓库：`git@github.com:Rynkio-dot/auto-project.git`
