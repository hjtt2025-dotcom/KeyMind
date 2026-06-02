import sys   
import os
import json
import socket
import threading
from datetime import datetime
from pynput import keyboard, mouse
from PIL import Image, ImageDraw
import pystray
# 引入 PyQt5 核心库以及全新添加的 Web 引擎库
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel, QGraphicsDropShadowEffect, QPushButton, QFileDialog, QMessageBox
from PyQt5.QtCore import Qt, QRect, QEasingCurve, QPoint, QVariantAnimation, QPropertyAnimation, pyqtSignal, QObject, QEvent, QUrl, pyqtSlot, QSettings, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel

# 线程通信中转站，确保按键统计能安全更新界面
class Communicate(QObject):
    update_count = pyqtSignal(int)
    update_theme = pyqtSignal(str)
    update_dynamic = pyqtSignal(bool)
    show_key_event = pyqtSignal(str)
    hide_osd_event = pyqtSignal()
    level_up_event = pyqtSignal(int, str)

class BackendBridge(QObject):
    @pyqtSlot(bool)
    def set_level_osd_enabled(self, enable):
        settings = QSettings("KeyMind", "Settings")
        settings.setValue("level_osd_enabled", enable)

    @pyqtSlot(result=bool)
    def get_level_osd_enabled(self):
        settings = QSettings("KeyMind", "Settings")
        raw_val = settings.value("level_osd_enabled", True)  # 默认开启
        return str(raw_val).lower() == 'true' if isinstance(raw_val, str) else bool(raw_val)

    @pyqtSlot(bool)
    def set_level_sound_enabled(self, enable):
        settings = QSettings("KeyMind", "Settings")
        settings.setValue("level_sound_enabled", enable)

    @pyqtSlot(result=bool)
    def get_level_sound_enabled(self):
        settings = QSettings("KeyMind", "Settings")
        raw_val = settings.value("level_sound_enabled", True)
        return str(raw_val).lower() == 'true' if isinstance(raw_val, str) else bool(raw_val)

    @pyqtSlot()
    def import_data(self):
        global records, count, today_date
        file_path, _ = QFileDialog.getOpenFileName(None, "导入本地数据", "", "JSON Files (*.json)")
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    imported_data = json.load(f)
                
                if not isinstance(imported_data, dict):
                    raise ValueError("数据格式不正确，请选择有效的 JSON 文件")
                    
                # 智能高水位融合（高阶玩法）：
                # 遍历导入的数据，如果本地没有这个日期，直接追加；
                # 如果本地有，比较哪边敲击量大，保留大的那份。既防重复导入，又防覆盖掉今天正在打字的新数据。
                for date_key, day_data in imported_data.items():
                    if date_key not in records:
                        records[date_key] = day_data
                    else:
                        local_total = records[date_key].get("total", 0)
                        imported_total = day_data.get("total", 0)
                        if imported_total > local_total:
                            records[date_key] = day_data
                
                # 重新对齐今天的计数器
                if today_date in records:
                    count = records[today_date].get("total", 0)
                    
                # 物理写入并触发内存刷新（强制穿透15秒墙）
                save_data_files(force=True)
                comm.update_count.emit(count) # 触发 update_count 会连带将全量数据推送给前端
                
                # 抛弃丑陋的原生弹窗和暴力的重载，直接唤起前端的高级提示窗
                if hasattr(window, 'dash_window') and window.dash_window.isVisible():
                    full_data_json = json.dumps(records, ensure_ascii=False)
                    msg = "智能高水位融合完毕！<br>已自动为你保留最完整的数据记录。"
                    js = f"window.appData = {full_data_json}; showCustomNotice('导入成功', '{msg}', false);"
                    window.dash_window.page().runJavaScript(js)
            except Exception as e:
                if hasattr(window, 'dash_window') and window.dash_window.isVisible():
                    err_msg = str(e).replace('\n', '<br>').replace("'", "\\'")
                    js = f"showCustomNotice('导入失败', '{err_msg}', true);"
                    window.dash_window.page().runJavaScript(js)

    @pyqtSlot()
    def export_json(self):
        global records
        if not records:
            return
            
        file_path, _ = QFileDialog.getSaveFileName(None, "导出全量数据备份", "KeyMind_Backup.json", "JSON Files (*.json)")
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(records, f, ensure_ascii=False, indent=4)
                
                if hasattr(window, 'dash_window') and window.dash_window.isVisible():
                    safe_path = file_path.replace('\\', '/').replace("'", "\\'")
                    msg = f"全量数据已安全备份至：<br><span style=\\'font-size:12px;color:#888;\\'>{safe_path}</span><br><br>你可以随时通过“导入”功能恢复这些数据。"
                    js = f"showCustomNotice('导出成功', '{msg}', false);"
                    window.dash_window.page().runJavaScript(js)
            except Exception as e:
                if hasattr(window, 'dash_window') and window.dash_window.isVisible():
                    err_msg = str(e).replace('\n', '<br>').replace("'", "\\'")
                    js = f"showCustomNotice('导出失败', '{err_msg}', true);"
                    window.dash_window.page().runJavaScript(js)

    @pyqtSlot()
    def clear_all_data(self):
        global records, count, today_date
        records.clear()
        records[today_date] = init_today_data() # 保留今日空壳，防止崩溃
        count = 0
        save_data_files(force=True)
        comm.update_count.emit(count)
        
        # 强制刷新前端图表页面，让数据瞬间蒸发
        if hasattr(window, 'dash_window') and window.dash_window.isVisible():
            window.dash_window.page().runJavaScript("location.reload();")

    @pyqtSlot(bool)
    def set_auto_start(self, enable):
        if sys.platform == "win32":
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "KeyMind"
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
                if enable:
                    # 判断是 python 脚本还是打包后的 exe，确保幽灵后台运行
                    if sys.argv[0].endswith('.py'):
                        cmd = f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{os.path.abspath(sys.argv[0])}"'
                    else:
                        cmd = f'"{os.path.abspath(sys.argv[0])}"'
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, app_name)
                    except FileNotFoundError:
                        pass
                winreg.CloseKey(key)
            except Exception as e:
                print("设置开机启动失败:", e)

    @pyqtSlot(result=bool)
    def get_auto_start(self):
        if sys.platform == "win32":
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "KeyMind"
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
                winreg.QueryValueEx(key, app_name)
                winreg.CloseKey(key)
                return True
            except FileNotFoundError:
                return False
        return False

                

    @pyqtSlot(int)
    def set_retention(self, days):
        settings = QSettings("KeyMind", "Settings")
        settings.setValue("retention_days", days)
        cleanup_old_records()
        save_data_files(force=True)

    @pyqtSlot(result=int)
    def get_retention(self):
        settings = QSettings("KeyMind", "Settings")
        return settings.value("retention_days", 0, type=int)

    @pyqtSlot(bool)
    def set_hide_on_close(self, enable):
        settings = QSettings("KeyMind", "Settings")
        settings.setValue("hide_on_close", enable)

    @pyqtSlot(result=bool)
    def get_hide_on_close(self):
        settings = QSettings("KeyMind", "Settings")
        raw_val = settings.value("hide_on_close", True)
        return str(raw_val).lower() == 'true' if isinstance(raw_val, str) else bool(raw_val)

    @pyqtSlot(bool)
    def set_osd_enabled(self, enable):
        settings = QSettings("KeyMind", "Settings")
        settings.setValue("osd_enabled", enable)
        osd._is_enabled_cached = enable # 强制更新缓存
        if not enable:
            comm.hide_osd_event.emit()

    @pyqtSlot(result=bool)
    def get_osd_enabled(self):
        settings = QSettings("KeyMind", "Settings")
        raw_osd = settings.value("osd_enabled", True)
        return str(raw_osd).lower() == 'true' if isinstance(raw_osd, str) else bool(raw_osd)
    
    @pyqtSlot(str)
    def set_dynamic_config(self, config_str):
        settings = QSettings("KeyMind", "Settings")
        settings.setValue("dynamic_config", config_str)
        if hasattr(window, '_cached_stages'):
            delattr(window, '_cached_stages') # 清除缓存，使下次渲染应用新规则
        comm.update_dynamic.emit(True) # 强制刷新胶囊渲染

    @pyqtSlot(result=str)
    def get_dynamic_config(self):
        settings = QSettings("KeyMind", "Settings")
        return settings.value("dynamic_config", "", type=str)

    @pyqtSlot(str)
    def set_capsule_theme(self, bg_color):
        settings = QSettings("KeyMind", "Settings")
        settings.setValue("capsule_bg", bg_color)
        comm.update_theme.emit(bg_color)

    @pyqtSlot(result=str)
    def get_capsule_theme(self):
        settings = QSettings("KeyMind", "Settings")
        return settings.value("capsule_bg", "rgba(255, 255, 255, 0.95)", type=str)

    @pyqtSlot(bool)
    def set_dynamic_color(self, enable):
        settings = QSettings("KeyMind", "Settings")
        settings.setValue("dynamic_color", enable)
        settings.sync() # 强制同步，防止读写并发引发的时间差
        if not enable:
            # 只要切回静态模式，直接把注册表里最新的胶囊颜色糊到内存上
            bg = settings.value("capsule_bg", "rgba(255, 255, 255, 0.95)", type=str)
            comm.update_theme.emit(bg)
        comm.update_dynamic.emit(enable)

    @pyqtSlot(result=bool)
    def get_dynamic_color(self):
        settings = QSettings("KeyMind", "Settings")
        raw_dyn = settings.value("dynamic_color", False)
        return str(raw_dyn).lower() == 'true' if isinstance(raw_dyn, str) else bool(raw_dyn)
try:
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lock_socket.bind(("127.0.0.1", 47200))
except OSError:
    os._exit(0)

if getattr(sys, 'frozen', False):
    CURRENT_DIR = os.path.dirname(sys.executable)
else:
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_NAME = os.path.join(CURRENT_DIR, "count_record.json")


DATA_JS_NAME = os.path.join(CURRENT_DIR, "data.js")
today_date = datetime.now().strftime("%Y-%m-%d")
records = {}

# --- 新增：配置文件与清理逻辑 ---
CONFIG_FILE = os.path.join(CURRENT_DIR, "config.json")
app_config = {"retention_days": 0} # 0 代表默认永久保留

# 读取配置文件
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            app_config = json.load(f)
    except:
        pass

def clean_old_data():
    global records, app_config
    days = app_config.get("retention_days", 0)
    if days <= 0:
        return # 永久保留，直接跳过
        
    current_date = datetime.now()
    keys_to_delete = []
    
    # 遍历所有记录，挑出超期的日期
    for date_str in records.keys():
        try:
            record_date = datetime.strptime(date_str, "%Y-%m-%d")
            if (current_date - record_date).days > days:
                keys_to_delete.append(date_str)
        except ValueError:
            continue # 防止有非日期格式的异常key
            
    # 集中删除并保存
    if keys_to_delete:
        for k in keys_to_delete:
            del records[k]
        save_data_files()
# -----------------------------

# 全局变量，用于隐形计算心流专注时间
last_input_time = datetime.now()
current_focus_start = datetime.now()

def init_today_data():
    # 提前生成0到23的字符串字典，代表一天的24小时
    hourly_data = {str(i): {"total": 0, "keys": {}, "mouse": {"left": 0, "right": 0, "scroll": 0}} for i in range(24)}
    return {
        "total": 0,
        "keys": {},
        "mouse": {"left": 0, "right": 0, "scroll": 0},
        "focus_time": 0,          # 记录单日最长连续专注心流（分钟）
        "hourly": hourly_data     # 记录24小时每小时的详细分发数据
    }

if os.path.exists(FILE_NAME):
    try:
        with open(FILE_NAME, "r", encoding="utf-8") as f:
            records = json.load(f)
        # 兼容旧版本数据，防止报错
        if isinstance(records.get(today_date), int):
            old_count = records[today_date]
            records[today_date] = init_today_data()
            records[today_date]["total"] = old_count
        elif today_date not in records:
            records[today_date] = init_today_data()
    except:
        records[today_date] = init_today_data()
else:
    records[today_date] = init_today_data()

count = records[today_date]["total"]

# 初始化RPG等级跟踪
LEVEL_THRESHOLDS = [0, 100, 500, 1000, 2000, 3500, 5500, 8000, 11000, 15000, 20000, 30000, 45000, 60000, 80000, 100000, 120000, 145000, 170000, 200000, 230000, 265000, 300000, 340000, 380000, 420000, 465000, 510000, 560000, 620000, 680000, 740000, 800000, 860000, 920000, 990000, 1050000, 1150000, 1250000, 1350000, 1450000, 1550000, 1650000, 1750000, 1850000, 1950000, 2050000, 2150000, 2250000, 2350000, 2500000, 2650000, 2800000, 3000000, 3200000, 3400000, 3600000, 3800000, 4000000, 4300000, 4600000, 5000000, 5500000, 6000000, 7000000, 8000000, 9000000, 10000000, 11000000, 12000000, 13000000, 14000000, 15000000, 15500000, 16000000, 17000000, 18000000, 19000000, 20000000, 21000000, 22000000, 23000000, 24000000, 25000000, 26000000, 27000000, 28000000, 29000000, 30000000, 31000000, 32000000, 33000000, 34000000, 35000000, 36000000, 37000000, 40000000, 43000000, 46000000, 50000000]
LEVEL_TITLES = ["键盘萌新"]*4 + ["打字学徒"]*4 + ["键盘熟手"]*4 + ["键盘熟练工"]*4 + ["指尖修行者"]*4 + ["盲打练习生"]*4 + ["盲打小成"]*4 + ["手速觉醒者"]*4 + ["键盘老手"]*4 + ["人肉输入法"]*4 + ["弹指神通传人"]*4 + ["键盘艺术家"]*4 + ["键道修行者"]*4 + ["键道小成"]*4 + ["键道大成"]*4 + ["键盘飞升者"]*40

all_time_total = sum(d.get("total", 0) for d in records.values())
last_rpg_level = 1
for i, threshold in enumerate(LEVEL_THRESHOLDS):
    if all_time_total >= threshold:
        last_rpg_level = i + 1
    else:
        break

def cleanup_old_records():
    global records, today_date
    settings = QSettings("KeyMind", "Settings")
    days = settings.value("retention_days", 0, type=int)
    if days == 0:
        return
    
    from datetime import datetime as dt
    try:
        today_obj = dt.strptime(today_date, "%Y-%m-%d")
    except Exception:
        return
        
    keys_to_delete = []
    for date_str in list(records.keys()):
        try:
            record_date = dt.strptime(date_str, "%Y-%m-%d")
            if (today_obj - record_date).days > days:
                keys_to_delete.append(date_str)
        except ValueError:
            continue
            
    if keys_to_delete:
        for k in keys_to_delete:
            del records[k]

cleanup_old_records()

import time

# 在全局变量区域新增时间戳
last_save_time = 0

def save_data_files(force=False):
    global last_save_time
    current_time = time.time()
    
    # 物理防护墙：限制最少 15 秒才能进行一次物理磁盘写入，保护固态硬盘
    if not force and current_time - last_save_time < 15:
        return
        
    last_save_time = current_time
    
    try:
        # 【核心修复】：必须先序列化为字符串。如果直接写入文件时触发 RuntimeError，会造成源文件被截断清空（彻底损坏数据）！
        json_str = json.dumps(records, ensure_ascii=False, indent=4)
        js_str = "window.appData = " + json.dumps(records, ensure_ascii=False) + ";"
        
        # 储存给程序内部用的数据
        with open(FILE_NAME, "w", encoding="utf-8") as f:
            f.write(json_str)
        # 额外生成一个 js 文件，这是为了绕过浏览器的本地跨域限制，让 HTML 直接读取真实数据！
        with open(DATA_JS_NAME, "w", encoding="utf-8") as f:
            f.write(js_str)
    except RuntimeError:
        # 字典如果在序列化时被其他线程修改，会在 dumps 阶段被拦截，安全放弃本次写入，保护原文件
        pass


class KeyStrokeOSD(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # 指针触碰瞬间，化身为“小手”选中状态
        self.setCursor(Qt.PointingHandCursor)
        
        self.label = QLabel("", self)
        self.label.setStyleSheet("""
            background-color: rgba(255, 255, 255, 0.95);
            color: #1d1d1f;
            padding: 16px 36px;
            border-radius: 16px;
            font-family: '-apple-system', 'Microsoft YaHei UI', sans-serif;
            font-size: 24px;
            font-weight: 800;
            border: 1px solid rgba(0, 0, 0, 0.06);
        """)
        
        self.shadow = QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(20)
        self.shadow.setColor(QColor(0, 0, 0, 40))
        self.shadow.setOffset(0, 6)
        self.label.setGraphicsEffect(self.shadow)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(self.label)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.fade_out)
        
        self.custom_pos = None 
        self.drag_start_pos = None
        
        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(300)
        self.opacity_anim.setStartValue(1.0)
        self.opacity_anim.setEndValue(0.0)
        self.opacity_anim.finished.connect(self.hide)

    def fade_out(self):
        self.opacity_anim.start()

    @pyqtSlot()
    def force_hide(self):
        # 核心清空逻辑：销毁的同时遗忘自定义坐标，下次生成直接归位
        self.custom_pos = None
        self.hide()

    def enterEvent(self, event):
        self.timer.stop()
        self.opacity_anim.stop()
        self.setWindowOpacity(1.0)

    def leaveEvent(self, event):
        # 核心拦截：如果菜单正在打开或处于拖拽状态，无视系统的离开判定，绝不启动销毁定时器
        if not getattr(self, 'is_menu_open', False) and not getattr(self, 'is_dragging', False):
            self.timer.start(1000)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.timer.stop()
            self.opacity_anim.stop()
            self.setWindowOpacity(1.0)
            self.drag_start_pos = event.globalPos() - self.pos()
            event.accept()
        # 爆破点：植入右键菜单


        elif event.button() == Qt.RightButton:
            from PyQt5.QtWidgets import QMenu
            
            # 菜单激活时：上锁！暂停衰变，满血驻留
            self.is_menu_open = True
            self.timer.stop()
            self.opacity_anim.stop()
            self.setWindowOpacity(1.0)
            
            menu = QMenu(self)
            # 彻底放大菜单：巨型加粗字体 + 更宽的点击热区 + 护眼圆角
            menu.setStyleSheet("""
                QMenu { 
                    background: rgba(255, 255, 255, 0.98); 
                    border: 1px solid rgba(0, 0, 0, 0.08); 
                    border-radius: 14px; 
                    padding: 4px; 
                }
                QMenu::item { 
                    padding: 12px 24px; 
                    border-radius: 8px; 
                    font-family: '-apple-system', 'Microsoft YaHei UI', sans-serif; 
                    font-size: 26px; 
                    font-weight: 900; 
                    color: #1d1d1f; 
                }
                QMenu::item:selected { 
                    background: #fee2e2; 
                    color: #dc2626; 
                }
            """)
            disable_action = menu.addAction("🚫 禁用悬浮窗")
            action = menu.exec_(event.globalPos())
            
            # 菜单执行完毕，立刻解除状态锁
            self.is_menu_open = False
            
            if action == disable_action:
                settings = QSettings("KeyMind", "Settings")
                settings.setValue("osd_enabled", False)
                self.force_hide()
            else:
                # 菜单消失且没禁用时：只有当鼠标确实已经移出悬浮窗，才重启倒计时
                if not self.underMouse():
                    self.timer.start(1000)
            event.accept()



    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.drag_start_pos:
            new_pos = event.globalPos() - self.drag_start_pos
            screen_rect = QApplication.primaryScreen().availableGeometry()
            new_x = max(screen_rect.x(), min(new_pos.x(), screen_rect.right() - self.width() + 1))
            new_y = max(screen_rect.y(), min(new_pos.y(), screen_rect.bottom() - self.height() + 1))
            self.move(new_x, new_y)
            self.custom_pos = QPoint(new_x, new_y)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.is_dragging = False
        self.drag_start_pos = None
        if not self.underMouse() and not getattr(self, 'is_menu_open', False):
            self.timer.start(1000)

    @pyqtSlot(str)
    def show_key(self, event_text):
        # 【CPU减负】：使用内存标记，直接拦截高频的硬盘与注册表读取
        if not hasattr(self, '_is_enabled_cached'):
            settings = QSettings("KeyMind", "Settings")
            raw_osd = settings.value("osd_enabled", True)
            self._is_enabled_cached = str(raw_osd).lower() == 'true' if isinstance(raw_osd, str) else bool(raw_osd)
            
        if not self._is_enabled_cached:
            return

        # 补丁：只要带有“鼠标”或“滚动”字眼的，统统不加键盘外框
        if "鼠标" in event_text or "滚动" in event_text:
            self.label.setText(event_text)
        else:
            # 建立直觉翻译映射表，把过长的机器名缩短成人类习惯的称呼
            name_map = {
                'backspace': '删除', 'enter': '回车', 'space': '空格', 
                'caps_lock': '大写锁定', 'shift': 'Shift', 'ctrl_l': 'Ctrl', 
                'alt_l': 'Alt', 'cmd_l': 'Win', 'esc': 'Esc', 'tab': 'Tab', 
                'up': '↑', 'down': '↓', 'left': '←', 'right': '→', 
                'print_screen': '截屏', 'delete': 'Del', 'insert': 'Ins',
                'page_up': 'PgUp', 'page_down': 'PgDn', 'fn': 'Fn',
                'media_volume_mute': '静音', 'media_volume_down': '音量-', 'media_volume_up': '音量+',
                'media_play_pause': '播放/暂停', 'media_previous': '上一曲', 'media_next': '下一曲'
            }
            display_name = name_map.get(event_text, event_text.upper())
            self.label.setText(f"⌨️  {display_name} 键 按下")
            
        self.adjustSize()
        
        if self.custom_pos:
            self.move(self.custom_pos)
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.x() + screen.width() - self.width() - 20
            y = screen.y() + screen.height() - self.height() - 50
            self.move(x, y)
        
        self.opacity_anim.stop()
        self.setWindowOpacity(1.0)
        self.show()
        
        # 图层防反超机制：只要升级弹窗存在，就强制把它托起到最顶层，绝对不被当前按键气泡遮挡
        if 'level_osd' in globals() and globals()['level_osd'].isVisible():
            globals()['level_osd'].raise_()
            
        self.timer.start(1000)

class LevelUpOSD(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        
        self.label = QLabel("", self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("""
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 20px;
            padding: 28px 48px;
            font-family: '-apple-system', 'Microsoft YaHei UI', sans-serif;
        """)
        
        self.shadow = QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(30)
        self.shadow.setColor(QColor(0, 0, 0, 25))
        self.shadow.setOffset(0, 10)
        self.label.setGraphicsEffect(self.shadow)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.addWidget(self.label)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.fade_out)
        
        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(400)
        self.opacity_anim.finished.connect(self.check_hide)
        
        self.pos_anim = QPropertyAnimation(self, b"pos")
        self.pos_anim.setDuration(3500)
        self.pos_anim.setEasingCurve(QEasingCurve.OutExpo)

    def check_hide(self):
        if self.windowOpacity() == 0.0:
            self.hide()

    def fade_out(self):
        self.opacity_anim.stop()
        self.opacity_anim.setStartValue(1.0)
        self.opacity_anim.setEndValue(0.0)
        self.opacity_anim.start()

    @pyqtSlot(int, str)
    def show_level_up(self, level, title):
        settings = QSettings("KeyMind", "Settings")
        raw_val = settings.value("level_osd_enabled", True)
        is_enabled = str(raw_val).lower() == 'true' if isinstance(raw_val, str) else bool(raw_val)
        if not is_enabled:
            return
        if level < 20:
            main_c = "#64748b"
        elif level < 40:
            main_c = "#16a34a"
        elif level < 60:
            main_c = "#0284c7"
        elif level < 80:
            main_c = "#9333ea"
        elif level < 95:
            main_c = "#dc2626"
        else:
            main_c = "#ca8a04"

        self.label.setStyleSheet("""
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 20px;
            padding: 28px 48px;
            font-family: '-apple-system', 'Microsoft YaHei UI', sans-serif;
        """)

        html = f"""
        <table cellpadding="0" cellspacing="0">
            <tr>
                <td valign="middle" style="padding-right: 32px;">
                    <div style='font-size: 64px;'>🎉</div>
                </td>
                <td valign="middle">
                    <div style='font-size: 22px; font-weight: 800; color: #1a1a1a; margin-bottom: 4px;'>恭喜升级！</div>
                    <div style='font-size: 38px; font-weight: 900; color: {main_c}; margin-bottom: 2px;'>Lv.{level}</div>
                    <div style='font-size: 16px; font-weight: 700; color: #64748b;'>解锁称号：{title}</div>
                
                
                </td>
            </tr>
        </table>
        """
        self.label.setText(html)
        self.adjustSize()
        
        screen = QApplication.primaryScreen().availableGeometry()
        # 目标停留位置：标准右下角，紧贴任务栏上方
        end_x = screen.x() + screen.width() - self.width() - 30
        end_y = screen.y() + screen.height() - self.height() - 60
        
        # 动画起始位置：在目标位置正下方 40 像素处，短距离微滑
        start_x = end_x
        start_y = end_y + 40
        
        self.move(start_x, start_y)
        self.opacity_anim.stop()
        self.opacity_anim.setStartValue(0.0)
        self.opacity_anim.setEndValue(1.0)
        self.opacity_anim.start()
        
        self.pos_anim.stop()
        self.pos_anim.setStartValue(QPoint(start_x, start_y))
        self.pos_anim.setEndValue(QPoint(end_x, end_y))
        self.pos_anim.start()
        
        self.show()
        self.raise_()
        self.timer.start(4000)
        
        raw_sound = settings.value("level_sound_enabled", True)
        sound_enabled = str(raw_sound).lower() == 'true' if isinstance(raw_sound, str) else bool(raw_sound)
        
        if sound_enabled and sys.platform == "win32":
            import winsound
            import os
            from PyQt5.QtCore import QTimer
            
            wav_path = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Media', 'Ring01.wav')
            if os.path.exists(wav_path):
                # 1. 异步播放 Ring01
                winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                # 2. 物理掐断：1000毫秒后强制向声卡发送 None 终止播放，只留第一声
                QTimer.singleShot(2000, lambda: winsound.PlaySound(None, winsound.SND_PURGE))
            else:
                winsound.MessageBeep(winsound.MB_OK)

class DynamicMoonIsland(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        self.base_height = 70
        self.expand_height = 82
        self.base_width = 0
        self.expand_width = 0
        self.old_pos = None
        self.anim = None
        self.is_docked = False
        self.dock_edge = None
        self.pos_anim = None
        
        settings = QSettings("KeyMind", "Settings")
        self.bg_color = settings.value("capsule_bg", "rgba(255, 255, 255, 0.95)", type=str)
        raw_dyn = settings.value("dynamic_color", False)
        self.dynamic_enabled = str(raw_dyn).lower() == 'true' if isinstance(raw_dyn, str) else bool(raw_dyn)
        
        # 核心拦截：在绘制第一帧 UI 之前，立刻结算一次动态心流发光的最终颜色
        if self.dynamic_enabled:
            global count
            cfg_str = settings.value("dynamic_config", "", type=str)
            stages = []
            if cfg_str:
                try:
                    stages = json.loads(cfg_str)
                    stages.sort(key=lambda x: int(x['threshold']))
                except: pass
            
            if not stages:
                stages = [
                    {"threshold": 0, "color": "rgba(255, 255, 255, 0.95)"},
                    {"threshold": 100, "color": "rgba(248, 244, 238, 0.95)"},
                    {"threshold": 1000, "color": "rgba(224, 235, 245, 0.95)"},
                    {"threshold": 3000, "color": "rgba(134, 239, 172, 0.95)"},
                    {"threshold": 8000, "color": "rgba(126, 214, 253, 0.95)"},
                    {"threshold": 15000, "color": "rgba(249, 115, 22, 0.95)"},
                    {"threshold": 30000, "color": "rgba(239, 68, 68, 0.95)"},
                    {"threshold": 50000, "color": "rgba(39, 39, 42, 0.95)"}
                ]
            
            active_color = stages[0]['color'] if stages else "#ffffff"
            for st in stages:
                if count >= int(st['threshold']):
                    active_color = st['color']
            self.bg_color = active_color

        self.init_ui()

    def set_theme(self, bg_color):
        self.bg_color = bg_color
        self.update_island_size()

    def set_dynamic(self, enable):
        self.dynamic_enabled = enable
        global count
        self.refresh_ui(count)

    def init_ui(self):
        self.container = QWidget(self)
        self.container.setObjectName("island")
        self.container.setCursor(Qt.PointingHandCursor) # 新增：鼠标触碰到胶囊整体时切换成小手指
        
        layout = QHBoxLayout(self.container)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(0)

        # 左侧圆圈：改为关闭/隐藏功能
        self.icon_btn = QPushButton("◎")
        self.icon_btn.setCursor(Qt.PointingHandCursor)
        self.icon_btn.clicked.connect(self.hide_to_tray)
        
        # 中间数字
        global count
        self.num_label = QLabel(str(count))
        self.num_label.setAlignment(Qt.AlignCenter)
        
        # 右侧菜单：保持统计功能
        self.menu_btn = QPushButton("≡")
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        self.menu_btn.clicked.connect(self.open_dashboard)

        # 苹果系高级感字体（剥离写死的颜色，交由 update 统一接管）
        apple_font = '-apple-system, "SF Pro Display", "SF Pro Icons", "Helvetica Neue", "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif'
        self.icon_btn.setStyleSheet("QPushButton { font-size: 28px; font-weight: 500; border: none; background: transparent; }")
        self.num_label.setStyleSheet(f"font-family: {apple_font}; font-size: 32px; font-weight: 600;")
        self.menu_btn.setStyleSheet("QPushButton { font-size: 28px; font-weight: 500; border: none; background: transparent; }")

        layout.addWidget(self.icon_btn)
        layout.addStretch()
        layout.addWidget(self.num_label)
        layout.addStretch()
        layout.addWidget(self.menu_btn)

        self.update_island_size()

        self.shadow = QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(15)
        self.shadow.setColor(QColor(0, 0, 0, 40))
        self.shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(self.shadow)
        
        self.container.setStyleSheet(f"QWidget#island {{ background: {self.bg_color}; border-radius: {int(self.base_height/2)}px; border: 1px solid rgba(0, 0, 0, 0.05); }}")
        self.icon_btn.installEventFilter(self)
        self.menu_btn.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj in (self.icon_btn, self.menu_btn):
            if event.type() == QEvent.MouseButtonPress:
                self.mousePressEvent(event)
            elif event.type() == QEvent.MouseMove:
                self.mouseMoveEvent(event)
            elif event.type() == QEvent.MouseButtonRelease:
                self.mouseReleaseEvent(event)
                # 新增：如果刚才发生了拖动，直接拦截掉释放事件，防止按钮触发误点击
                if getattr(self, 'is_dragged', False):
                    return True
        return super().eventFilter(obj, event)



    def update_island_size(self):
        metrics = self.num_label.fontMetrics()
        content_w = metrics.boundingRect(self.num_label.text()).width()
        self.num_label.setFixedWidth(content_w + 10) 
        self.base_width = content_w + 175 
        self.expand_width = self.base_width + 20
        
        is_hover = self.underMouse()
        curr_w = self.expand_width if is_hover else self.base_width
        curr_h = self.expand_height if is_hover else self.base_height
        
        self.container.setGeometry(20, 20, curr_w, curr_h)
        self.setFixedSize(self.expand_width + 100, self.expand_height + 100)
        
        curr_r = int(curr_h / 2)
        self.container.setStyleSheet(f"QWidget#island {{ background: {self.bg_color}; border-radius: {curr_r}px; border: 1px solid rgba(0, 0, 0, 0.05); }}")
        
        # 智能反色引擎：同步前端的判定规则，剥离深海大师球的暗色判定，让中间的字恢复黑色
        is_dark = any(k in self.bg_color.lower() for k in ['black', '30, 30, 30', '65, 81', '75, 85, 99', '63, 63, 70', '82, 82, 91', '39, 39, 42', '23, 23, 23', '333399', '434343', '764ba2'])
        text_color = "#ffffff" if is_dark else "#1d1d1f"
        icon_color = "rgba(255, 255, 255, 0.8)" if is_dark else "#5d5d5d"
        menu_color = "rgba(255, 255, 255, 0.6)" if is_dark else "rgba(29, 29, 31, 120)"
        
        apple_font = '-apple-system, "SF Pro Display", "SF Pro Icons", "Helvetica Neue", "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif'
        curr_f = 35 if is_hover else 32
        icon_f = curr_f - 4
        self.num_label.setStyleSheet(f"font-family: {apple_font}; font-size: {curr_f}px; font-weight: 600; color: {text_color};")
        self.icon_btn.setStyleSheet(f"QPushButton {{ color: {icon_color}; font-size: {icon_f}px; font-weight: 500; border: none; background: transparent; }}")
        self.menu_btn.setStyleSheet(f"QPushButton {{ color: {menu_color}; font-size: {icon_f}px; font-weight: 500; border: none; background: transparent; }}")

    def refresh_ui(self, new_count):
        self.num_label.setText(str(new_count))
        
        # 记录旧颜色和宽度，用于比对
        old_color = getattr(self, 'bg_color', None)
        
        if getattr(self, 'dynamic_enabled', False):
            # 【CPU减负】：引入缓存机制，拒绝每次按键都去读取硬盘/注册表并解析 JSON
            if not hasattr(self, '_cached_stages'):
                import json
                settings = QSettings("KeyMind", "Settings")
                cfg_str = settings.value("dynamic_config", "", type=str)
                stages = []
                if cfg_str:
                    try:
                        stages = json.loads(cfg_str)
                        stages.sort(key=lambda x: int(x['threshold']))
                    except: pass
                
                # 如果没有自定义，使用全新的苹果高级感质感
                if not stages:
                    stages = [
                        {"threshold": 0, "color": "rgba(255, 255, 255, 0.95)"},
                        {"threshold": 100, "color": "rgba(248, 244, 238, 0.95)"},
                        {"threshold": 1000, "color": "rgba(224, 235, 245, 0.95)"},
                        {"threshold": 3000, "color": "rgba(134, 239, 172, 0.95)"},
                        {"threshold": 8000, "color": "rgba(126, 214, 253, 0.95)"},
                        {"threshold": 15000, "color": "rgba(249, 115, 22, 0.95)"},
                        {"threshold": 30000, "color": "rgba(239, 68, 68, 0.95)"},
                        {"threshold": 50000, "color": "rgba(39, 39, 42, 0.95)"}
                    ]
                self._cached_stages = stages
            
            ## 动态匹配最近的阈值
            active_color = self._cached_stages[0]['color'] if self._cached_stages else "#ffffff"
            for st in self._cached_stages:
                if new_count >= int(st['threshold']):
                    active_color = st['color']
            self.bg_color = active_color
            
        # 【CPU减负】：仅当数字宽度发生明显变化（比如进位）或颜色改变时，才触发极度耗费性能的 setStyleSheet 树重绘
        metrics = self.num_label.fontMetrics()
        content_w = metrics.boundingRect(str(new_count)).width()
        current_fixed_w = self.num_label.width()
        
        if old_color != getattr(self, 'bg_color', None) or abs(content_w + 10 - current_fixed_w) > 5:
            self.update_island_size()

        # --- 究极进化：向 HTML 引擎实时注入今日全量数据，同步刷新所有看板和图表 ---
        if hasattr(self, 'dash_window') and self.dash_window.isVisible():
            import time
            if not hasattr(self, '_last_js_inject_time'):
                self._last_js_inject_time = 0
            
            # 【CPU减负】：引入节流阀。限制向 Chromium 注入的最高帧率为 10帧/秒 (100ms)。视觉无感，且 CPU 占用率暴降！
            if time.time() - self._last_js_inject_time < 0.1:
                return
            self._last_js_inject_time = time.time()
            
            global records, today_date
            import json
            
            # 为了极致性能，每次敲击只把“今天”的数据打包发给前端，前端会自动与历史数据合并
            today_data_json = json.dumps(records.get(today_date, {}), ensure_ascii=False)
            
            js_code = f"""
            (function() {{
                const d = new Date();
                const todayLocal = new Date(d.getTime() - (d.getTimezoneOffset() * 60000)).toISOString().split('T')[0];
                
                // 1. 局部更新前端内存中的数据
                if(!window.appData) window.appData = {{}};
                window.appData[todayLocal] = {today_data_json};
                const realData = window.appData[todayLocal];

                // 2. 更新今日概览与时间段
                var countDisplay = document.getElementById('total-count');
                if (countDisplay) countDisplay.innerText = realData.total.toLocaleString();
                
                var focusTimeEl = document.querySelector('.card:nth-child(2) .number-normal');
                if (focusTimeEl) focusTimeEl.innerHTML = `${{realData.focus_time || 0}}<span class="unit">分钟</span>`;

                let maxVol = -1, peakHour = 0;
                if (realData.hourly) {{
                    for (let i = 0; i < 24; i++) {{
                        let v = realData.hourly[String(i)] ? realData.hourly[String(i)].total : 0;
                        if (v > maxVol) {{ maxVol = v; peakHour = i; }}
                    }}
                }}
                var activeTimeEl = document.querySelector('.card:nth-child(3) .number-normal');
                if (activeTimeEl) activeTimeEl.innerHTML = `${{String(peakHour).padStart(2, '0')}}:00 - ${{String(peakHour+1).padStart(2, '0')}}:00`;

                // 3. 更新高频键位
                if (realData.keys) {{
                    let sortedKeys = Object.entries(realData.keys).sort((a, b) => b[1] - a[1]).slice(0, 3);
                    var hotkeyListEl = document.querySelector('.hotkey-list');
                    if (hotkeyListEl && sortedKeys.length > 0) {{
                        hotkeyListEl.innerHTML = sortedKeys.map(k => `<div class="key-tag">${{k[0].toUpperCase()}} <span class="key-count">${{k[1]}}</span></div>`).join('');
                    }}
                }}

                // 4. 更新历史最高与总数
                let allTimeTotal = 0; let maxDayVol = 0; let maxDayDate = '-';
                for (let date in window.appData) {{
                    let vol = window.appData[date].total || 0;
                    allTimeTotal += vol;
                    if (vol > maxDayVol) {{ maxDayVol = vol; maxDayDate = date; }}
                }}
                var histTotalEl = document.getElementById('history-total');
                if (histTotalEl) histTotalEl.innerText = allTimeTotal.toLocaleString();
                var histMaxEl = document.getElementById('history-max');
                if (histMaxEl) histMaxEl.innerText = maxDayVol.toLocaleString();
                var histMaxDateEl = document.getElementById('history-max-date');
                if (histMaxDateEl) histMaxDateEl.innerText = '📅 创造于 ' + maxDayDate;

                // 4.5 实时更新经验条、等级与头衔
                if (typeof window.updateRPGSystem === 'function') {{
                    window.updateRPGSystem(allTimeTotal);
                }}

                // 5. 更新按键类型统计条
                if (realData.keys) {{
                    let lCount = 0, sCount = 0, dCount = 0, cCount = 0;
                    for (let k in realData.keys) {{
                        let v = realData.keys[k];
                        if (k.length === 1 && /[a-z]/i.test(k)) lCount += v;
                        else if (k === 'backspace' || k === 'delete' || k === 'del') dCount += v;
                        else if (k.length === 1) sCount += v;
                        else cCount += v;
                    }}
                    let tk = lCount + sCount + dCount + cCount;
                    if (tk > 0) {{
                        let p1 = Math.round((lCount/tk)*100), p2 = Math.round((sCount/tk)*100), p3 = Math.round((dCount/tk)*100);
                        let p4 = 100 - p1 - p2 - p3;
                        var compBar = document.querySelector('.composition-bar');
                        if (compBar) compBar.innerHTML = `<div class="comp-segment" style="width: ${{p1}}%; background: #86aef4;"></div><div class="comp-segment" style="width: ${{p2}}%; background: #ab92f4;"></div><div class="comp-segment" style="width: ${{p3}}%; background: #fad28c;"></div><div class="comp-segment" style="width: ${{p4}}%; background: #e0e0e0;"></div>`;
                        var legendArea = document.querySelector('.legend-area');
                        if (legendArea) legendArea.innerHTML = `<div class="legend-item"><div class="legend-dot" style="background: #86aef4;"></div>字母文字 ${{p1}}%</div><div class="legend-item"><div class="legend-dot" style="background: #ab92f4;"></div>符号数字 ${{p2}}%</div><div class="legend-item"><div class="legend-dot" style="background: #fad28c;"></div>退格删除 ${{p3}}%</div><div class="legend-item"><div class="legend-dot" style="background: #e0e0e0;"></div>功能控制 ${{p4}}%</div>`;
                    }}
                }}

                // 6. 动态更新图表 (仅当该图表所在页面被选中时触发重绘，节约显卡算力)
                if (typeof chartData !== 'undefined' && realData.hourly) {{
                    chartData.length = 0; 
                    for (let i = 0; i < 24; i += 2) {{
                        let h1 = realData.hourly[String(i)] ? realData.hourly[String(i)].total : 0;
                        let h2 = realData.hourly[String(i+1)] ? realData.hourly[String(i+1)].total : 0;
                        let vol = h1 + h2;
                        chartData.push({{ time: `${{String(i).padStart(2, '0')}}:00-${{String(i+2).padStart(2, '0')}}:00`, volume: vol, speed: Math.round(vol / 120) }});
                    }}
                    if (typeof drawChart === 'function' && document.getElementById('tab-analysis').classList.contains('active')) drawChart();
                }}

                if (typeof historyData !== 'undefined') {{
                    const dayNames = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
                    for (let i = 6; i >= 0; i--) {{
                        let d2 = new Date(); d2.setDate(d2.getDate() - i);
                        let dateStr = new Date(d2.getTime() - (d2.getTimezoneOffset() * 60000)).toISOString().split('T')[0];
                        let shortDate = (d2.getMonth() + 1) + '-' + d2.getDate();
                        let vol = (window.appData && window.appData[dateStr]) ? window.appData[dateStr].total : 0;
                        historyData[6-i] = {{ date: i === 0 ? '今日' : shortDate, week: dayNames[d2.getDay()], volume: vol }};
                    }}
                    if (typeof drawHistoryChart === 'function' && document.getElementById('tab-history').classList.contains('active')) drawHistoryChart();
                }}
                // 7. 更新输入热力图的 24 小时色块
                const heatmapContainer = document.getElementById('heatmap-container');
                if (heatmapContainer && document.getElementById('tab-heatmap').classList.contains('active')) {{
                    const blocks = heatmapContainer.querySelectorAll('.heat-block');
                    if (blocks.length === 24) {{
                        for (let i = 0; i < 24; i++) {{
                            let hData = realData.hourly[String(i)] || {{ total: 0, keys: {{}}, mouse: {{}} }};
                            let keys = hData.keys || {{}}; let mouse = hData.mouse || {{}};
                            let keyVol = hData.total || 0;
                            let mouseVol = (mouse.left || 0) + (mouse.right || 0) + (mouse.scroll || 0);
                            let vol = keyVol + mouseVol;
                            let hType = 'none'; let hIntensity = 0;
                            if (vol > 0) {{
                                hIntensity = Math.max(0.2, Math.min(vol / 1500, 1.0));
                                let wasd = (keys['w']||0) + (keys['a']||0) + (keys['s']||0) + (keys['d']||0);
                                let codingKeys = (keys['ctrl_l']||0) + (keys['alt_l']||0) + (keys['tab']||0) + (keys['esc']||0);
                                let enterKeys = keys['enter'] || 0; let scrolls = mouse.scroll || 0;
                                let designKeys = (keys['space']||0) + (keys['shift']||0) + (keys['ctrl_l']||0) + (keys['alt_l']||0) + (keys['z']||0);
                                if (keyVol > 0 && wasd > keyVol * 0.25) {{ hType = 'game'; blocks[i].dataset.desc = '高频 WASD，可能在激战游戏'; }}
                                else if (keyVol > 0 && designKeys > keyVol * 0.2 && (mouse.left || 0) > vol * 0.1) {{ hType = 'design'; blocks[i].dataset.desc = '高频修饰键与点击，设计创作中'; }}
                                else if (keyVol > 0 && codingKeys > keyVol * 0.1) {{ hType = 'code'; blocks[i].dataset.desc = '频繁组合键，沉浸编程或办公'; }}
                                else if (scrolls > vol * 0.5) {{ hType = 'read'; blocks[i].dataset.desc = '大量滚轮滑动，轻松阅读中'; }}
                                else if (keyVol > 0 && enterKeys > keyVol * 0.05) {{ hType = 'chat'; blocks[i].dataset.desc = '频繁回车，碎片化沟通'; }}
                                else if (keyVol > 0) {{ hType = 'type'; blocks[i].dataset.desc = '流畅输出，高速码字中'; }}
                                else {{ hType = 'read'; blocks[i].dataset.desc = '键鼠轻度浏览中'; }}
                            }} else {{
                                blocks[i].dataset.desc = '该时段设备无操作';
                            }}
                            blocks[i].dataset.type = hType;
                            
                            const colorMap = {{ 'game': '239, 68, 68', 'code': '16, 185, 129', 'type': '59, 130, 246', 'read': '168, 85, 247', 'design': '6, 182, 212', 'chat': '236, 72, 153', 'video': '245, 158, 11', 'none': '226, 232, 240' }};
                            let alpha = hType === 'none' ? 1 : (hIntensity * 0.7 + 0.3);
                            blocks[i].style.background = hType === 'none' ? '#e2e8f0' : `rgba(${{colorMap[hType]}}, ${{alpha}})`;
                        }}
                    }}
                }}

                // 8. 精准爆破：实时更新当前打开的键盘/鼠标硬件弹窗
                const modal = document.getElementById('heatmapModal');
                if (modal && modal.classList.contains('show') && window.currentModalHourIndex !== undefined) {{
                    if (window.currentModalHourIndex === 'wholeday') {{
                        const btnWholeDay = document.getElementById('btn-wholeday-heatmap');
                        if (btnWholeDay) btnWholeDay.click();
                    }} else {{
                        let hData = realData.hourly[window.currentModalHourIndex] || {{ keys: {{}}, mouse: {{}} }};
                        let keyNameMap = {{ 'Space': 'space', 'Enter': 'enter', 'Shift': 'shift', 'Ctrl': 'ctrl', 'Alt': 'alt', 'Win': 'cmd', 'Caps': 'caps_lock', 'Del': 'delete', 'Ins': 'insert', 'PgUp': 'page_up', 'PgDn': 'page_down', 'PrtSc': 'print_screen', 'ScrLk': 'scroll_lock', 'Back': 'backspace', 'Esc': 'esc', 'Tab': 'tab', '↑': 'up', '↓': 'down', '←': 'left', '→': 'right', 'Fn': 'fn', 'Menu': 'menu', 'Pause': 'pause', '~': '`' }};
                    
                    document.querySelectorAll('.kb-key[data-raw-k]').forEach(el => {{
                                let rawK = el.getAttribute('data-raw-k');
                                let mappedKey = rawK === '~' ? '`' : rawK;
                                let searchKey = keyNameMap[mappedKey] || mappedKey.toLowerCase();
                                if (searchKey === 'cmd_l') searchKey = 'cmd'; 
                                if (searchKey === 'ctrl_l') searchKey = 'ctrl';
                                if (searchKey === 'alt_l') searchKey = 'alt';
                                
                                let hourKeys = hData.keys || {{}};
                                let hitCount = (hourKeys[searchKey] || 0) + 
                                               (hourKeys[searchKey + '_l'] || 0) + 
                                               (hourKeys[searchKey + '_r'] || 0) + 
                                               (searchKey.includes('_l') ? (hourKeys[searchKey.replace('_l', '_r')] || 0) : 0);
                                if (rawK === '~') hitCount += (hourKeys['~'] || 0);
                                if (searchKey === 'shift') hitCount += (hourKeys['shift_r'] || 0) + (hourKeys['shift_l'] || 0);


                        el.setAttribute('data-hits', hitCount);
                        if (hitCount === 0) {{ el.style.background = 'linear-gradient(rgba(0,0,0,0.03), rgba(0,0,0,0.03)), #ffffff'; el.style.color = '#666'; }}
                        else if (hitCount <= 10) {{ el.style.background = 'linear-gradient(rgba(186, 230, 253, 0.9), rgba(186, 230, 253, 0.9)), #ffffff'; el.style.color = '#0284c7'; }}
                        else if (hitCount <= 50) {{ el.style.background = 'linear-gradient(rgba(56, 189, 248, 0.9), rgba(56, 189, 248, 0.9)), #ffffff'; el.style.color = '#fff'; }}
                        else if (hitCount <= 150) {{ el.style.background = 'linear-gradient(rgba(52, 211, 153, 0.9), rgba(52, 211, 153, 0.9)), #ffffff'; el.style.color = '#fff'; }}
                        else if (hitCount <= 300) {{ el.style.background = 'linear-gradient(rgba(250, 204, 21, 0.9), rgba(250, 204, 21, 0.9)), #ffffff'; el.style.color = '#fff'; }}
                        else if (hitCount <= 500) {{ el.style.background = 'linear-gradient(rgba(251, 146, 60, 0.9), rgba(251, 146, 60, 0.9)), #ffffff'; el.style.color = '#fff'; }}
                        else {{ el.style.background = 'linear-gradient(rgba(239, 68, 68, 0.95), rgba(239, 68, 68, 0.95)), #ffffff'; el.style.color = '#fff'; }}
                    }});
                    
                    let leftC = (hData.mouse && hData.mouse.left) || 0;
                    let rightC = (hData.mouse && hData.mouse.right) || 0;
                    let scrolls = (hData.mouse && hData.mouse.scroll) || 0;
                    const getMouseStyle = (c) => {{
                        if (c === 0) return 'linear-gradient(rgba(0,0,0,0.03), rgba(0,0,0,0.03)), #ffffff';
                        if (c <= 50) return 'linear-gradient(rgba(186, 230, 253, 0.9), rgba(186, 230, 253, 0.9)), #ffffff';
                        if (c <= 150) return 'linear-gradient(rgba(56, 189, 248, 0.9), rgba(56, 189, 248, 0.9)), #ffffff';
                        if (c <= 300) return 'linear-gradient(rgba(52, 211, 153, 0.9), rgba(52, 211, 153, 0.9)), #ffffff';
                        if (c <= 600) return 'linear-gradient(rgba(250, 204, 21, 0.9), rgba(250, 204, 21, 0.9)), #ffffff';
                        if (c <= 1000) return 'linear-gradient(rgba(251, 146, 60, 0.9), rgba(251, 146, 60, 0.9)), #ffffff';
                        return 'linear-gradient(rgba(239, 68, 68, 0.95), rgba(239, 68, 68, 0.95)), #ffffff';
                        }};
                    let mL = document.querySelector('.mouse-btn-left'); if(mL) {{ mL.setAttribute('data-hits', leftC); mL.style.background = getMouseStyle(leftC); }}
                    let mR = document.querySelector('.mouse-btn-right'); if(mR) {{ mR.setAttribute('data-hits', rightC); mR.style.background = getMouseStyle(rightC); }}
                    let mW = document.querySelector('.mouse-wheel'); if(mW) {{ mW.setAttribute('data-hits', scrolls); mW.style.background = getMouseStyle(scrolls); }}
                    
                    let mStats = document.querySelector('.mouse-stats');
                    if (mStats) mStats.innerHTML = `左键：<span>${{leftC}}</span> 次<br>右键：<span>${{rightC}}</span> 次<br>滚轮：<span>${{scrolls}}</span> 刻`;

                    // 实时刷新悬浮气泡的内容（如果鼠标刚好悬停在某个按键上）
                    let hwTooltip = document.getElementById('hardwareTooltip');
                    if (hwTooltip && hwTooltip.style.display === 'block' && window.currentHoveredHardware) {{
                        let name = window.currentHoveredHardware.getAttribute('data-name');
                        let hits = window.currentHoveredHardware.getAttribute('data-hits');
                        hwTooltip.innerHTML = `<span style="font-size: 15px; font-weight: bold; color: #1a1a1a;">${{name}}</span> <span style="color: #888; margin: 0 4px;">:</span> <span style="font-weight: 600; color: #4a8df5; font-size: 14px;">${{hits}} 次/刻</span>`;
                    }}
                }}
                }}
            }})();
            """
            self.dash_window.page().runJavaScript(js_code)

    def open_dashboard(self):
        # 智能获取当前屏幕的缩放比例，提早计算好倍数
        screen = QApplication.primaryScreen()
        scale_factor = screen.logicalDotsPerInch() / 96.0

        # 确保我们只创建一个独立窗口，如果已经打开了就直接把它拉到屏幕最前面
        if not hasattr(self, 'dash_window'):
            self.dash_window = QWebEngineView()
            
            # 开启 Chromium 引擎内核级平滑滚动动画，找回原生浏览器的丝滑阻尼感
            self.dash_window.settings().setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, True)
            
            self.dash_window.setWindowTitle("KeyMind - 数据看板")
            # 核心修复：让应用外框的物理大小也同步按比例放大，防止内部网页空间被过度挤压
            self.dash_window.resize(int(850 * scale_factor), int(550 * scale_factor))
            
            # 搭建 QWebChannel 通信桥梁
            self.channel = QWebChannel()
            self.backend = BackendBridge()
            self.channel.registerObject("backend", self.backend)
            self.dash_window.page().setWebChannel(self.channel)
        
        import sys
        if getattr(sys, 'frozen', False):
            # 如果是打包后的 exe，强制获取 exe 所在的真实物理路径
            current_dir = os.path.dirname(sys.executable)
        else:
            # 如果是纯源码运行，获取 py 文件所在路径
            current_dir = os.path.dirname(os.path.abspath(__file__))
        # 智能加载引擎：自动寻找当前目录下最新的 KeyMind HTML 文件，彻底消灭版本号硬编码陷阱
        html_files = [f for f in os.listdir(current_dir) if f.startswith('KeyMind') and f.endswith('.html')]
        latest_html = max(html_files, key=lambda f: os.path.getmtime(os.path.join(current_dir, f))) if html_files else "KeyMind.html"
        file_path = os.path.join(current_dir, latest_html) 
        self.dash_window.load(QUrl.fromLocalFile(file_path))
        
        # 只对网页内部进行放大，绝不影响胶囊！
        self.dash_window.setZoomFactor(scale_factor)
        
        self.dash_window.show()
        self.dash_window.activateWindow()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.open_dashboard()

    def hide_to_tray(self):
        settings = QSettings("KeyMind", "Settings")
        
        # 修复：摒弃原生的 type=bool，统一使用全兼容的字符串降级解析法
        raw_hide = settings.value("hide_on_close", True)
        allow_hide = str(raw_hide).lower() == 'true' if isinstance(raw_hide, str) else bool(raw_hide)
        
        # 如果不允许隐藏，就果断拔电源退出
        if not allow_hide:
            if hasattr(self, 'dash_window'):
                self.dash_window.close()
            save_data_files(force=True)
            os._exit(0)
            
        self.hide()
        
        # 托盘逻辑：弃用抢占主线程的 pystray，改用 PyQt5 原生托盘
        if not hasattr(self, 'tray_icon'):
            from PyQt5.QtWidgets import QSystemTrayIcon, QMenu
            from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
            
            self.tray_icon = QSystemTrayIcon(self)
            
            # 重新绘制：高对比度“键盘剪影”托盘图标，确保小尺寸下像素锐利
            pixmap = QPixmap(64, 64)
            pixmap.fill(QColor("transparent"))
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            
            # 1. 绘制键盘外壳底板 (暗岩灰)
            painter.setBrush(QColor("#334155"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(4, 16, 56, 32, 6, 6)
            
            # 2. 绘制纯白高对比键帽阵列
            painter.setBrush(QColor("#ffffff"))
            
            # 第一排按键 (4个小方块)
            painter.drawRoundedRect(10, 21, 8, 5, 2, 2)
            painter.drawRoundedRect(22, 21, 8, 5, 2, 2)
            painter.drawRoundedRect(34, 21, 8, 5, 2, 2)
            painter.drawRoundedRect(46, 21, 8, 5, 2, 2)
            
            # 第二排按键 (4个小方块)
            painter.drawRoundedRect(10, 29, 8, 5, 2, 2)
            painter.drawRoundedRect(22, 29, 8, 5, 2, 2)
            painter.drawRoundedRect(34, 29, 8, 5, 2, 2)
            painter.drawRoundedRect(46, 29, 8, 5, 2, 2)
            
            # 底部灵魂空格键 (长条)
            painter.drawRoundedRect(10, 37, 44, 6, 2, 2)
            
            painter.end()
            self.tray_icon.setIcon(QIcon(pixmap))
            
            # 注入灵魂：添加鼠标悬浮身份牌
            self.tray_icon.setToolTip("KeyMind - 键盘统计")
            
            # 核心修复：将局部变量绑定为 self 的成员属性，对抗 Python 垃圾回收机制
            self.tray_menu = QMenu()
            show_action = self.tray_menu.addAction("显示胶囊")
            show_action.triggered.connect(self.show_from_tray)
            quit_action = self.tray_menu.addAction("彻底退出")
            quit_action.triggered.connect(lambda: [save_data_files(force=True), os._exit(0)])
            
            self.tray_icon.setContextMenu(self.tray_menu)
            
            # 兼容双击托盘图标恢复
            self.tray_icon.activated.connect(
                lambda reason: self.show_from_tray() if reason == QSystemTrayIcon.DoubleClick else None
            )

        self.tray_icon.show()

    def show_from_tray(self):
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        self.show()
        global count
        comm.update_count.emit(count)

    def enterEvent(self, event):
        self.animate_size(True)
        if self.is_docked:
            self.animate_dock(show=True)

    def leaveEvent(self, event):
        self.animate_size(False)
        if self.is_docked:
            self.animate_dock(show=False)

    def animate_dock(self, show):
        c_rect = self.container.geometry()
        current_x = self.x()
        target_y = self.y()
        screen_rect = QApplication.primaryScreen().availableGeometry()
        
        visible_width = 38
        
        if show:
            if self.dock_edge == 'left':
                target_x = screen_rect.x() - c_rect.x()
            else:
                target_x = screen_rect.x() + screen_rect.width() - c_rect.width() - c_rect.x()
        else:
            if self.dock_edge == 'left':
                target_x = screen_rect.x() - (c_rect.x() + c_rect.width()) + visible_width
            else:
                target_x = screen_rect.x() + screen_rect.width() - c_rect.x() - visible_width
                
        self.pos_anim = QVariantAnimation(self)
        self.pos_anim.setDuration(200)
        self.pos_anim.setStartValue(current_x)
        self.pos_anim.setEndValue(target_x)
        self.pos_anim.setEasingCurve(QEasingCurve.OutQuad)
        self.pos_anim.valueChanged.connect(lambda v: self.move(int(v), target_y))
        self.pos_anim.start()

    def animate_size(self, is_expanding):
        start_w, end_w = (self.base_width, self.expand_width) if is_expanding else (self.expand_width, self.base_width)
        start_h, end_h = (self.base_height, self.expand_height) if is_expanding else (self.expand_height, self.base_height)
        start_font, end_font = (32, 35) if is_expanding else (35, 32)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(400) # 稍微加长一点，让回弹更舒展
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        # 爆破点：将原本生硬的 OutQuint 改为 OutBack，获得极具苹果质感的 Q弹 动画！
        self.anim.setEasingCurve(QEasingCurve.OutBack)
        def update_styles(v):
            curr_w = start_w + (end_w - start_w) * v
            curr_h = start_h + (end_h - start_h) * v
            curr_f = int(start_font + (end_font - start_font) * v)
            icon_f = curr_f - 4 
            curr_r = int(curr_h / 2)
            center_x, center_y = 20 + self.expand_width / 2, 20 + self.expand_height / 2
            self.container.setGeometry(int(center_x - curr_w / 2), int(center_y - curr_h / 2), int(curr_w), int(curr_h))
            self.container.setStyleSheet(f"QWidget#island {{ background: {self.bg_color}; border-radius: {curr_r}px; border: 1px solid rgba(0, 0, 0, 0.05); }}")
            
            # 动效期间每帧保持一致的反色判定（剥离深海大师球的暗色判定，让中间的字恢复黑色）
            is_dark = any(k in self.bg_color.lower() for k in ['black', '30, 30, 30', '65, 81', '75, 85, 99', '63, 63, 70', '82, 82, 91', '39, 39, 42', '23, 23, 23', '333399', '434343', '764ba2'])
            text_color = "#ffffff" if is_dark else "#1d1d1f"
            icon_color = "rgba(255, 255, 255, 0.8)" if is_dark else "#5d5d5d"
            menu_color = "rgba(255, 255, 255, 0.6)" if is_dark else "rgba(29, 29, 31, 120)"
            
            apple_font = '-apple-system, "SF Pro Display", "SF Pro Icons", "Helvetica Neue", "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif'
            self.num_label.setStyleSheet(f"font-family: {apple_font}; font-size: {curr_f}px; font-weight: 900; color: {text_color};")
            self.icon_btn.setStyleSheet(f"QPushButton {{ color: {icon_color}; font-size: {icon_f}px; font-weight: 500; border: none; background: transparent; }}")
            self.menu_btn.setStyleSheet(f"QPushButton {{ color: {menu_color}; font-size: {icon_f}px; font-weight: 500; border: none; background: transparent; }}")
        self.anim.valueChanged.connect(update_styles)
        self.anim.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: 
            self.old_pos = event.globalPos()
            self.drag_start_pos = event.globalPos() 
            self.is_docked = False
            self.is_dragged = False 
            
            if self.anim and self.anim.state() == QVariantAnimation.Running:
                self.anim.stop()
            
            geom = self.container.geometry()
            new_h = geom.height() - 4
            curr_r = int(new_h / 2)
            
            self.container.setGeometry(geom.x() + 2, geom.y() + 2, geom.width() - 4, new_h)
            self.container.setStyleSheet(f"QWidget#island {{ background: {self.bg_color}; border-radius: {curr_r}px; border: 1px solid rgba(0, 0, 0, 0.05); }}")
            self.shadow.setBlurRadius(5)
            self.shadow.setOffset(0, 1)
        elif event.button() == Qt.RightButton:
            from PyQt5.QtWidgets import QMenu
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu { 
                    background: rgba(255, 255, 255, 0.98); 
                    border: 1px solid rgba(0, 0, 0, 0.08); 
                    border-radius: 14px; 
                    padding: 8px; 
                }
                QMenu::item { 
                    padding: 14px 28px; 
                    border-radius: 8px; 
                    font-family: '-apple-system', 'Microsoft YaHei UI', sans-serif; 
                    font-size: 25px; 
                    font-weight: 600; 
                    color: #1d1d1f; 
                }
                QMenu::item:selected { 
                    background: rgba(0, 0, 0, 0.05); 
                }
            """)
            open_action = menu.addAction("📊 打开任务统计")
            close_action = menu.addAction("❌ 关闭 KeyMind")
            
            action = menu.exec_(event.globalPos())
            if action == open_action:
                self.open_dashboard()
            elif action == close_action:
                if hasattr(self, 'dash_window'):
                    self.dash_window.close()
                save_data_files(force=True)
                import os
                os._exit(0)

    def mouseMoveEvent(self, event):
        if self.old_pos:
            # 新增：判断移动距离，超过5个像素才被认定为真正的拖动
            if hasattr(self, 'drag_start_pos'):
                total_delta = event.globalPos() - self.drag_start_pos
                if total_delta.manhattanLength() > 5:
                    self.is_dragged = True

            delta = QPoint(event.globalPos() - self.old_pos)
            
            # 计算初步的新坐标
            new_x = self.x() + delta.x()
            new_y = self.y() + delta.y()
            
            # 获取屏幕和内部容器几何信息
            screen_rect = QApplication.primaryScreen().availableGeometry()
            c_rect = self.container.geometry()
            
            # 修正边界：考虑窗口内部边距，确保胶囊边缘精准贴合屏幕
            min_x = screen_rect.x() - c_rect.x()
            max_x = screen_rect.right() - (c_rect.x() + c_rect.width()) + 1
            min_y = screen_rect.y() - c_rect.y()
            max_y = screen_rect.bottom() - (c_rect.y() + c_rect.height()) + 1
            
            # 仅执行范围限制（碰撞），删除了 snap_dist 相关逻辑
            new_x = max(min_x, min(new_x, max_x))
            new_y = max(min_y, min(new_y, max_y))
            
            self.move(new_x, new_y)
            self.old_pos = event.globalPos()
    def mouseReleaseEvent(self, event):
        self.old_pos = None
        
        # 新增：松开鼠标时，借助我们已有的函数恢复正常的尺寸和悬浮阴影
        self.update_island_size()
        self.shadow.setBlurRadius(15)
        self.shadow.setOffset(0, 4)
        
        screen_rect = QApplication.primaryScreen().availableGeometry()
        c_rect = self.container.geometry()
        
        if self.x() + c_rect.x() <= screen_rect.x() + 2:
            self.is_docked = True
            self.dock_edge = 'left'
            self.animate_dock(show=False)
        elif self.x() + c_rect.x() + c_rect.width() >= screen_rect.x() + screen_rect.width() - 2:
            self.is_docked = True
            self.dock_edge = 'right'
            self.animate_dock(show=False)
        else:
            self.is_docked = False
            self.dock_edge = None


# 托盘支持函数
def run_tray_icon():
    def show_window(icon, item):
        icon.stop()
        comm.update_count.emit(count) # 触发信号恢复窗口
        window.show()

    def quit_app(icon, item):
        icon.stop()
        save_data_files(force=True)
        os._exit(0)

    image = Image.new('RGB', (64, 64), color='black')
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill='white')
    menu = pystray.Menu(pystray.MenuItem('显示面板', show_window), pystray.MenuItem('彻底退出', quit_app))
    icon = pystray.Icon("counter", image, "打字统计", menu)
    icon.run()

def check_date_and_focus():
    global count, today_date, records, last_input_time, current_focus_start
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    
    if current_date != today_date:
        today_date = current_date
        if today_date not in records or isinstance(records[today_date], int):
            records[today_date] = init_today_data()
        count = records[today_date]["total"]
        current_focus_start = now 
        
    # 计算专注心流时间（如果两次输入间隔超过5分钟没有动静，就视为一次专注结束）
    if (now - last_input_time).total_seconds() > 5 * 60:
        current_focus_start = now
    else:
        # 计算当前这波连续操作进行了多少分钟
        focus_minutes = int((now - current_focus_start).total_seconds() / 60)
        if "focus_time" not in records[today_date]:
            records[today_date]["focus_time"] = 0
        # 永远只保留最长的那一次记录
        if focus_minutes > records[today_date]["focus_time"]:
            records[today_date]["focus_time"] = focus_minutes
            
    last_input_time = now
    return str(now.hour)

# 在全局变量区域新增防连击集合
pressed_keys = set()

def on_press(key):
    global count, today_date, records, pressed_keys
    current_hour = check_date_and_focus()
    
    try:
        k = key.char
        if k is None:
            k = str(key).replace("Key.", "")
    except AttributeError:
        k = str(key).replace("Key.", "")
    
    k = str(k).lower()
    
    # --- Code Point Fixer 增强版：全量底层机器码归一化 ---
    raw_map = {
        '<255>': 'fn', '`': '~', 
        'cmd_r': 'cmd_l', 'cmd': 'cmd_l', '<91>': 'cmd_l', '<92>': 'cmd_l',
        'ctrl_r': 'ctrl_l', 'ctrl': 'ctrl_l', '<162>': 'ctrl_l', '<163>': 'ctrl_l',
        'alt_r': 'alt_l', 'alt_gr': 'alt_l', 'alt': 'alt_l', '<164>': 'alt_l', '<165>': 'alt_l',
        'shift_r': 'shift', '<160>': 'shift', '<161>': 'shift',
        '<96>': '0', '<97>': '1', '<98>': '2', '<99>': '3', '<100>': '4', 
        '<101>': '5', '<102>': '6', '<103>': '7', '<104>': '8', '<105>': '9',
        '<106>': '*', '<107>': '+', '<109>': '-', '<110>': '.', '<111>': '/',
        '<173>': '静音', '<174>': '音量-', '<175>': '音量+', 
        '<176>': '下一曲', '<177>': '上一曲', '<179>': '播放/暂停',
        'media_volume_mute': '静音', 'media_volume_down': '音量-', 'media_volume_up': '音量+',
        'media_next': '下一曲', 'media_previous': '上一曲', 'media_play_pause': '播放/暂停'
    }
    k = raw_map.get(k, k)
    
    # --- 核心拦截：状态锁防连击 ---
    if k in pressed_keys:
        return  # 如果按键已经在按下的状态里，说明这是长按产生的系统连击，直接丢弃！
    pressed_keys.add(k)
    
    comm.show_key_event.emit(k) # 触发屏幕右下角提示
    
    count += 1
    records[today_date]["total"] = count
    
    if "hourly" not in records[today_date]:
        records[today_date]["hourly"] = {str(i): {"total": 0, "keys": {}, "mouse": {"left": 0, "right": 0, "scroll": 0}} for i in range(24)}
        
    records[today_date]["hourly"][current_hour]["total"] += 1
    
    if k not in records[today_date]["keys"]:
        records[today_date]["keys"][k] = 0
    records[today_date]["keys"][k] += 1
    
    if k not in records[today_date]["hourly"][current_hour]["keys"]:
        records[today_date]["hourly"][current_hour]["keys"][k] = 0
    records[today_date]["hourly"][current_hour]["keys"][k] += 1
    
    comm.update_count.emit(count)
    save_data_files()
    
    # 结算 RPG 等级
    global last_rpg_level
    all_time = sum(d.get("total", 0) for d in records.values())
    current_level = 1
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if all_time >= threshold:
            current_level = i + 1
        else:
            break
            
    if last_rpg_level > 0 and current_level > last_rpg_level:
        title = LEVEL_TITLES[current_level - 1] if current_level - 1 < len(LEVEL_TITLES) else "键盘之神"
        comm.level_up_event.emit(current_level, title)
    last_rpg_level = current_level

def on_release(key):
    global pressed_keys
    try:
        k = key.char
        if k is None:
            k = str(key).replace("Key.", "")
    except AttributeError:
        k = str(key).replace("Key.", "")
    k = str(k).lower()
    
    # 释放时也要执行全量归一化，才能在防连击集合里准确找到它并解锁
    raw_map = {
        '<255>': 'fn', '`': '~', 
        'cmd_r': 'cmd_l', 'cmd': 'cmd_l', '<91>': 'cmd_l', '<92>': 'cmd_l',
        'ctrl_r': 'ctrl_l', 'ctrl': 'ctrl_l', '<162>': 'ctrl_l', '<163>': 'ctrl_l',
        'alt_r': 'alt_l', 'alt_gr': 'alt_l', 'alt': 'alt_l', '<164>': 'alt_l', '<165>': 'alt_l',
        'shift_r': 'shift', '<160>': 'shift', '<161>': 'shift',
        '<96>': '0', '<97>': '1', '<98>': '2', '<99>': '3', '<100>': '4', 
        '<101>': '5', '<102>': '6', '<103>': '7', '<104>': '8', '<105>': '9',
        '<106>': '*', '<107>': '+', '<109>': '-', '<110>': '.', '<111>': '/',
        '<173>': '静音', '<174>': '音量-', '<175>': '音量+', 
        '<176>': '下一曲', '<177>': '上一曲', '<179>': '播放/暂停',
        'media_volume_mute': '静音', 'media_volume_down': '音量-', 'media_volume_up': '音量+',
        'media_next': '下一曲', 'media_previous': '上一曲', 'media_play_pause': '播放/暂停'
    }
    k = raw_map.get(k, k)
    
    if k in pressed_keys:
        pressed_keys.remove(k) # 仅解除状态锁，计数工作已全部交接给 on_press

def on_click(x, y, button, pressed):
    if pressed:
        current_hour = check_date_and_focus()
        
        if "hourly" not in records[today_date]:
            records[today_date]["hourly"] = {str(i): {"total": 0, "keys": {}, "mouse": {"left": 0, "right": 0, "scroll": 0}} for i in range(24)}
            
        btn_name = "left" if button == mouse.Button.left else "right" if button == mouse.Button.right else "other"
        
        # --- 触发鼠标 OSD 监控弹窗 ---
        if button == mouse.Button.left:
            comm.show_key_event.emit("🖱️  鼠标 左键 点击")
        elif button == mouse.Button.right:
            comm.show_key_event.emit("🖱️  鼠标 右键 点击")
        elif button == mouse.Button.middle:
            comm.show_key_event.emit("🖱️  鼠标 中键 点击")
        else:
            comm.show_key_event.emit("🖱️  鼠标 侧键 点击")
            
        if btn_name in records[today_date]["mouse"]:
            records[today_date]["mouse"][btn_name] += 1
            records[today_date]["hourly"][current_hour]["mouse"][btn_name] += 1
            comm.update_count.emit(count)
            save_data_files()

def on_scroll(x, y, dx, dy):
    current_hour = check_date_and_focus()
    
    if "hourly" not in records[today_date]:
        records[today_date]["hourly"] = {str(i): {"total": 0, "keys": {}, "mouse": {"left": 0, "right": 0, "scroll": 0}} for i in range(24)}
        
    # --- 触发滚轮 OSD 监控弹窗 ---
    if dy > 0:
        comm.show_key_event.emit("🖱️  向上 滚动")
    else:
        comm.show_key_event.emit("🖱️  向下 滚动")
        
    records[today_date]["mouse"]["scroll"] += 1
    records[today_date]["hourly"][current_hour]["mouse"]["scroll"] += 1
    comm.update_count.emit(count)
    save_data_files()

if __name__ == '__main__':
    # 核心引擎破解：向底层的 Chromium 注入命令行参数
    import sys
    sys.argv.append("--enable-smooth-scrolling")     # 强制开启平滑滚动引擎
    sys.argv.append("--enable-gpu-rasterization")    # 强制开启 GPU 硬件光栅化渲染
    sys.argv.append("--enable-zero-copy")            # 开启零拷贝显存优化（提升帧率）
    
    app = QApplication(sys.argv)
    
    # 防止程序多开


    app.setQuitOnLastWindowClosed(False)
    comm = Communicate()
    window = DynamicMoonIsland()
    osd = KeyStrokeOSD() # 实例化按键提示器
    level_osd = LevelUpOSD() # 实例化桌面升级弹窗
    
    # 绑定信号：当按键增加时，通知灵动岛刷新
    comm.level_up_event.connect(level_osd.show_level_up)
    comm.update_count.connect(window.refresh_ui)
    comm.update_theme.connect(window.set_theme)
    comm.update_dynamic.connect(window.set_dynamic)
    comm.show_key_event.connect(osd.show_key) # 绑定屏幕右下角提示
    comm.hide_osd_event.connect(osd.force_hide) # 信号接通：一键强制抹除悬浮窗，同时清空坐标
    
    # 核心补丁：在窗口显示前，强制发射一次当前的击键数据
    # 这将立刻触发 refresh_ui，让胶囊在开机瞬间就计算并渲染出你设定的专属颜色
    comm.update_count.emit(count)
    
    window.show()
    
    keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
    keyboard_listener.start()
    mouse_listener.start()
    
    # 刚启动时，先输出一次初始数据文件供 HTML 使用
    save_data_files()
    
    sys.exit(app.exec_())