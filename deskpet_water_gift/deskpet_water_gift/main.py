"""DeskPet v2 - fire and water desktop pets that chat with each other.

Runs 100% locally: no API keys, no cloud server. The two pets talk directly over TCP,
and Tailscale (free private network) lets two PCs on different Wi-Fi networks reach each other.

  Fire  = host. Waits for the other pet to connect.
  Water = client. Connects to the host's Tailscale IP.

Double-click the pet to type. Anything that isn't a known command is sent as a chat message.
Use "say <text>" to force sending a message (e.g. "say time to eat").
"""
import sys
import os
import re
import json
import traceback
import faulthandler
import math
import hmac
import random
import shutil
import subprocess
import datetime
import urllib.parse
import webbrowser
from pathlib import Path

import psutil
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRectF, QPointF, QPoint, QLockFile
from PySide6.QtGui import (QPainter, QPixmap, QColor, QImage, QRadialGradient,
                           QLinearGradient, QBrush, QPen)
from PySide6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress, QAbstractSocket
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit,
                               QMenu, QInputDialog)

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
DATA_DIR = Path.home() / ".deskpet"
CONFIG_FILE = DATA_DIR / "config.json"
NOTES_FILE = DATA_DIR / "notes.txt"
CHAT_LOG = DATA_DIR / "chat.log"

INPUT_HIDE_MS = 3000     # chat box hides itself after this much idle time

DEFAULTS = {
    "role": "",              # "fire" (host) or "water" (client); asked on first run
    "nickname": "",
    "host_ip": "",           # water only: the fire PC's Tailscale IP
    "port": 50505,
    "secret": "",            # shared passphrase, must be the same on both PCs
    "water_minutes": 45,
    "wander": True,          # pet strolls slowly around the desktop
    "scale": 1.0,            # avatar size: 0.5 to 2.5 (1.0 = normal)
    "opacity": 1.0,          # avatar opacity: 0.2 to 1.0
    "chat_first": True,      # True: typing sends a chat message unless you say my name or start with /
    "pos": None,             # last resting place [x, y], so the pet comes back where you left it
    "welcome_text": "",      # shown once on first launch (used by the gift package)
    "welcomed": False,
    "autostart_done": False,  # set after the pet installs its "start with Windows" entry once
    "sites": {
        "google": "https://www.google.com",
        "youtube": "https://www.youtube.com",
        "github": "https://github.com",
        "gmail": "https://mail.google.com",
        "maps": "https://maps.google.com",
    },
    "apps": {
        "notepad": "notepad", "calculator": "calc", "calc": "calc",
        "cmd": "cmd", "explorer": "explorer", "vscode": "code",
        "terminal": "wt", "paint": "mspaint",
    },
}
NICK_DEFAULT = {"fire": "Ember", "water": "Bubbles"}
JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "There are 10 kinds of people: those who know binary and those who don't.",
    "A SQL query walks into a bar, sees two tables and asks: can I join you?",
]


def load_config():
    DATA_DIR.mkdir(exist_ok=True)
    cfg = json.loads(json.dumps(DEFAULTS))
    gift = HERE / "gift_config.json"
    src = CONFIG_FILE if CONFIG_FILE.exists() else (gift if gift.exists() else None)
    if src:
        try:
            cfg.update(json.loads(src.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    DATA_DIR.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def to_seconds(n, unit):
    u = unit.lower()
    return n * 3600 if u.startswith("h") else n * 60 if u.startswith("m") else n


_crash_file = None


def install_crash_log():
    """pythonw has no console, so write any error to ~/.deskpet/error.log. Helps find why a pet vanished."""
    global _crash_file
    DATA_DIR.mkdir(exist_ok=True)
    log = DATA_DIR / "error.log"

    def hook(t, v, tb):
        try:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]\n"
                        + "".join(traceback.format_exception(t, v, tb)) + "\n")
        except Exception:
            pass
    sys.excepthook = hook
    try:
        if log.exists() and log.stat().st_size > 200_000:
            log.unlink()
        _crash_file = log.open("a", encoding="utf-8")
        faulthandler.enable(_crash_file)      # also records hard crashes
    except Exception:
        pass


def launch(target):
    try:
        if os.name == "nt":
            subprocess.Popen(f'start "" "{target}"', shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", target])
        else:
            exe = shutil.which(target)
            if not exe:
                return False
            subprocess.Popen([exe])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- networking
class Link(QObject):
    """Direct pet-to-pet connection. Newline-delimited JSON over TCP, shared-secret handshake."""
    message = Signal(str)
    status = Signal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.sock = None
        self.server = None
        self.buf = b""
        self.authed = False
        self.retry = QTimer(self)
        self.retry.timeout.connect(self.try_connect)

    def start(self):
        role = self.cfg["role"]
        if role == "fire":
            if self.server is None:
                self.server = QTcpServer(self)
                self.server.newConnection.connect(self.on_new)
            if not self.server.isListening():
                if not self.server.listen(QHostAddress.Any, int(self.cfg["port"])):
                    self.status.emit("Can't listen on that port")
                    return
            self.status.emit("Waiting for water to connect...")
        elif role == "water" and self.cfg["host_ip"]:
            self.retry.start(5000)
            self.try_connect()

    def stop(self):
        self.retry.stop()
        if self.sock:
            self.sock.abort()
        if self.server and self.server.isListening():
            self.server.close()

    def try_connect(self):
        if self.sock is not None:
            return
        s = QTcpSocket(self)
        s.connected.connect(lambda: self.attach(s))
        s.connectToHost(self.cfg["host_ip"], int(self.cfg["port"]))
        self.sock = s
        s.disconnected.connect(lambda: self.dropped(s))
        s.errorOccurred.connect(lambda _e: self.dropped(s))
        self.status.emit("Looking for fire...")

    def on_new(self):
        s = self.server.nextPendingConnection()
        if self.sock is not None:
            self.sock.abort()
        self.sock = s
        s.disconnected.connect(lambda: self.dropped(s))
        self.attach(s)

    def attach(self, s):
        s.setSocketOption(QAbstractSocket.KeepAliveOption, 1)
        s.readyRead.connect(lambda: self.on_read(s))
        self.authed = False
        self.buf = b""
        self.raw_send({"type": "hello", "secret": self.cfg["secret"]})

    def dropped(self, s):
        if s is not self.sock:
            return
        self.sock = None
        self.authed = False
        try:
            s.deleteLater()
        except RuntimeError:
            return  # socket already destroyed (app shutting down)
        self.status.emit("Offline" if self.cfg["role"] == "water" else "Waiting for water to connect...")

    def on_read(self, s):
        self.buf += bytes(s.readAll())
        if len(self.buf) > 65536:
            s.abort()
            return
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                data = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            if data.get("type") == "hello":
                ok = hmac.compare_digest(str(data.get("secret", "")), str(self.cfg["secret"]))
                if not ok:
                    self.status.emit("Wrong shared secret - connection refused")
                    s.abort()
                    return
                self.authed = True
                self.status.emit("Connected!")
            elif data.get("type") == "msg" and self.authed:
                self.message.emit(str(data.get("text", ""))[:500])

    def raw_send(self, obj):
        if self.sock is not None and self.sock.state() == QAbstractSocket.ConnectedState:
            self.sock.write((json.dumps(obj) + "\n").encode("utf-8"))
            return True
        return False

    def send(self, text):
        return self.authed and self.raw_send({"type": "msg", "text": text[:500]})


# ---------------------------------------------------------------- avatar
PET_HEIGHT = 92


def make_pet_pixmap(role, height=PET_HEIGHT, ratio=1.0):
    """Load the artwork, shrink it, and bake in soft shading so the flat art looks 3D-ish."""
    pm = QPixmap(str(ASSETS / f"{role}.png"))
    if pm.isNull():
        pm = QPixmap(150, 150)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setBrush(QColor("#ff8800" if role == "fire" else "#3cb4b4"))
        q.drawEllipse(5, 5, 140, 140)
        q.end()
    pm = pm.scaledToHeight(max(8, int(height * ratio)), Qt.SmoothTransformation)
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
    w, h = img.width(), img.height()
    q = QPainter(img)
    q.setCompositionMode(QPainter.CompositionMode_SourceAtop)  # only touches the pet's own pixels
    shine = QRadialGradient(w * 0.35, h * 0.28, w * 0.75)
    shine.setColorAt(0, QColor(255, 255, 255, 120))
    shine.setColorAt(1, QColor(255, 255, 255, 0))
    q.fillRect(0, 0, w, h, QBrush(shine))
    shade = QLinearGradient(0, h * 0.5, 0, h)
    shade.setColorAt(0, QColor(0, 0, 0, 0))
    shade.setColorAt(1, QColor(0, 0, 0, 80))
    q.fillRect(0, 0, w, h, QBrush(shade))
    q.end()
    out = QPixmap.fromImage(img)
    out.setDevicePixelRatio(ratio)      # drawn at the logical size but with extra pixels, so it stays sharp
    return out


class PetCanvas(QWidget):
    W, H = 150, 170

    def __init__(self, role, scale=1.0, opacity=1.0):
        super().__init__()
        self.role = role
        self.k = 1.0
        self.opacity = opacity
        self.set_scale(scale)
        self.t = 0
        self.hop = 0
        self.hop_h = 16
        self.wob_amp = 0.0
        self.wob_t = 0
        self.walking = False
        self.particles = []

    def set_scale(self, k):
        self.k = k
        self.setFixedSize(int(self.W * k), int(self.H * k))
        ratio = max(1.0, k * self.devicePixelRatioF())
        self.pm = make_pet_pixmap(self.role, PET_HEIGHT, ratio)   # logical height stays PET_HEIGHT
        self.update()

    def cheer(self, gentle=False):
        self.hop = 20
        self.hop_h = 5 if gentle else 16
        self.wob_amp = 0.07 if gentle else 0.18

    def lift(self):
        step = abs(math.sin(self.t * 0.10)) if self.walking else 0.0
        jump = abs(math.sin(self.hop / 20 * math.pi)) * self.hop_h if self.hop else 0.0
        return step * 5 + jump, step

    def tick(self):
        self.t += 1
        if self.hop > 0:
            self.hop -= 1
        if self.wob_amp > 0.003:
            self.wob_amp *= 0.94
            self.wob_t += 1
        else:
            self.wob_amp = 0.0
        self.spawn()
        for pt in self.particles:
            pt["life"] -= 1
            pt["y"] += pt["vy"]
            pt["x"] += pt["vx"] + (0.25 * math.sin(pt["ph"] + self.t * 0.1) if self.role == "water" else 0)
        self.particles = [pt for pt in self.particles if pt["life"] > 0]
        self.update()

    def spawn(self):
        if len(self.particles) > 25:
            return
        lift, _ = self.lift()
        top = self.H - 24 - lift - self.pm.height() / self.pm.devicePixelRatio()
        cx = self.W / 2
        if self.role == "water" and self.t % 9 == 0:
            life = random.randint(60, 90)
            self.particles.append(dict(x=cx + random.uniform(-22, 22), y=top + 14, vx=0, vy=-0.45,
                                       size=random.uniform(2.5, 5), life=life, max=life,
                                       ph=random.uniform(0, 6)))
        elif self.role == "fire" and self.t % 4 == 0:
            life = random.randint(25, 45)
            self.particles.append(dict(x=cx + random.uniform(-18, 18), y=top + 24, vx=random.uniform(-0.2, 0.2),
                                       vy=random.uniform(-1.0, -0.5), size=random.uniform(2, 3.5),
                                       life=life, max=life, ph=0))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.k, self.k)           # everything below is drawn in the base 150x170 space
        p.setOpacity(self.opacity)        # fades the pet only, so speech bubbles stay readable
        ground = self.H - 24
        lift, step = self.lift()
        idle = math.sin(self.t / 10)
        wob = self.wob_amp * math.sin(self.wob_t * 0.5)
        sx = 1 + 0.025 * idle + wob + (0.04 * (1 - step) if self.walking else 0)
        sy = 1 / sx   # squash and stretch keeps the "volume" the same, like jelly

        # soft shadow on the ground (shrinks when the pet hops)
        sw = 64 * (1 - min(lift, 30) / 80)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, int(70 * (1 - min(lift, 30) / 50))))
        p.drawEllipse(QRectF(self.W / 2 - sw / 2, ground - 5, sw, 10))

        p.save()
        p.translate(self.W / 2, ground - lift)
        p.scale(sx, sy)
        pw = self.pm.width() / self.pm.devicePixelRatio()
        ph = self.pm.height() / self.pm.devicePixelRatio()
        p.drawPixmap(QPointF(-pw / 2, -ph), self.pm)
        p.restore()

        for pt in self.particles:
            a = pt["life"] / pt["max"]
            r = pt["size"] * (1 if self.role == "water" else (0.4 + 0.6 * a))
            if self.role == "water":
                p.setPen(QPen(QColor(255, 255, 255, int(200 * a)), 1))
                p.setBrush(QColor(170, 235, 235, int(80 * a)))
                p.drawEllipse(QPointF(pt["x"], pt["y"]), r, r)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, int(220 * a)))
                p.drawEllipse(QPointF(pt["x"] - r * 0.35, pt["y"] - r * 0.35), r * 0.25, r * 0.25)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 120 + int(100 * (1 - a)), 40, int(255 * a)))
                p.drawEllipse(QPointF(pt["x"], pt["y"]), r, r)
        p.end()


# ---------------------------------------------------------------- main window
BUBBLE_STYLE = ("background:white; color:#222; border:3px solid #ccc; border-radius:14px;"
                "padding:10px; font-size:17px;")
COMPACT_STYLE = ("background:rgba(255,255,255,235); color:#333; border:2px solid #cfe; border-radius:11px;"
                 "padding:6px 8px; font-size:13px;")


class PetWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        if not self.cfg["role"] or not self.cfg["secret"]:
            self.setup_dialog()
        if not self.cfg["nickname"]:
            self.cfg["nickname"] = NICK_DEFAULT[self.cfg["role"]]
            save_config(self.cfg)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.drag_offset = None
        self.net_status = "Offline"
        self.menu_open = False
        self.target = None
        self.rest = 60
        self.moved = False
        self.cfg["scale"] = min(2.5, max(0.5, float(self.cfg.get("scale", 1.0))))
        self.cfg["opacity"] = min(1.0, max(0.2, float(self.cfg.get("opacity", 1.0))))
        self.fcx, self.fb = self.start_position()   # pet's horizontal centre, bottom edge of the window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.bubble = QLabel("")
        self.bubble.setWordWrap(True)
        self.bubble.setFixedWidth(300)
        self.bubble.setStyleSheet(BUBBLE_STYLE)
        self.bubble.hide()
        layout.addWidget(self.bubble, alignment=Qt.AlignHCenter)

        self.canvas = PetCanvas(self.cfg["role"], self.cfg["scale"], self.cfg["opacity"])
        layout.addWidget(self.canvas, alignment=Qt.AlignHCenter)

        self.input = QLineEdit()
        self.input.setFixedWidth(300)
        self.input.setPlaceholderText("Type a command or message...")
        self.input.setStyleSheet(
            "background:white; color:#222; border:3px solid #ccc; border-radius:12px;"
            "padding:8px; font-size:17px;")
        self.input.returnPressed.connect(self.submit)
        self.input.textEdited.connect(self.restart_input_timer)   # typing keeps the box open
        self.input.hide()
        layout.addWidget(self.input, alignment=Qt.AlignHCenter)

        self.anim = QTimer(self)
        self.anim.timeout.connect(self.frame)
        self.anim.start(33)

        self.bubble_timer = QTimer(self)
        self.bubble_timer.setSingleShot(True)
        self.bubble_timer.timeout.connect(self.hide_bubble)

        self.input_timer = QTimer(self)
        self.input_timer.setSingleShot(True)
        self.input_timer.timeout.connect(self.close_input)

        self.watch_timer = QTimer(self)             # keeps the pet from ever getting lost off-screen
        self.watch_timer.timeout.connect(self.watchdog)
        self.watch_timer.start(3000)

        self.water_timer = QTimer(self)
        self.water_timer.timeout.connect(self.water_reminder)
        self.set_water_interval(self.cfg["water_minutes"])

        self.link = Link(self.cfg)
        self.link.message.connect(self.on_peer_message)
        self.link.status.connect(self.on_status)
        self.link.start()

        wt = self.cfg.get("welcome_text")
        if wt and not self.cfg.get("welcomed"):
            self.cfg["welcomed"] = True
            save_config(self.cfg)
            self.say(wt, 30)
        else:
            self.say(f"Hi! I'm {self.cfg['nickname']}. Double-click me to type, or say 'help'.")

        self.ensure_startup()

    # ---------- layout + wandering ----------
    def relayout(self):
        self.adjustSize()
        self.place()

    def screen_at(self, x, y):
        """The screen the point is on (or the nearest one), so the pet stays on the monitor it is on."""
        pt = QPoint(int(x), int(y))
        best, best_d = None, None
        for sc in QApplication.screens():
            g = sc.geometry()
            if g.contains(pt):
                return sc
            dx = max(g.left() - pt.x(), 0, pt.x() - g.right())
            dy = max(g.top() - pt.y(), 0, pt.y() - g.bottom())
            d = dx * dx + dy * dy
            if best_d is None or d < best_d:
                best, best_d = sc, d
        return best or QApplication.primaryScreen()

    def cur_area(self):
        return self.screen_at(self.fcx, self.fb - 20).availableGeometry()

    def start_position(self):
        pos = self.cfg.get("pos")
        try:
            x, y = float(pos[0]), float(pos[1])
            if any(sc.geometry().contains(QPoint(int(x), int(y) - 20)) for sc in QApplication.screens()):
                return x, y
        except Exception:
            pass
        area = QApplication.primaryScreen().availableGeometry()
        return float(area.right() - 90), float(area.bottom() - 30)

    def remember_position(self):
        self.cfg["pos"] = [round(self.fcx), round(self.fb)]
        save_config(self.cfg)

    def watchdog(self):
        """Runs every few seconds: bring the pet back if it got hidden, lost off-screen, or covered."""
        if self.menu_open:
            return
        if not self.isVisible():
            self.show()
        if self.drag_offset is not None:
            return
        if not any(sc.geometry().contains(QPoint(int(self.fcx), int(self.fb - 20)))
                   for sc in QApplication.screens()):
            area = QApplication.primaryScreen().availableGeometry()   # its monitor was unplugged
            self.fcx, self.fb = float(area.right() - 90), float(area.bottom() - 30)
            self.target = None
            self.place()
        elif not any(sc.geometry().intersects(self.frameGeometry()) for sc in QApplication.screens()):
            self.place()
        if not self.input.isVisible():
            self.raise_()

    def place(self):
        area = self.cur_area()
        x = int(self.fcx - self.width() / 2)
        y = int(self.fb - self.height())
        x = max(area.left(), min(x, area.right() - self.width()))
        y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)

    def frame(self):
        self.canvas.tick()
        self.step()

    def busy(self):
        return (self.bubble.isVisible() or self.input.isVisible()
                or self.drag_offset is not None or self.menu_open)

    def step(self):
        if not self.cfg.get("wander", True) or self.busy():
            self.canvas.walking = False
            return
        if self.rest > 0:
            self.rest -= 1
            self.canvas.walking = False
            return
        area = self.cur_area()                       # only wander on the monitor the pet is on
        if self.target is None:
            half = self.canvas.width() / 2 + 10
            top = min(area.top() + self.canvas.height() + 120, area.bottom() - 40)
            self.target = (random.uniform(area.left() + half, max(area.left() + half, area.right() - half)),
                           random.uniform(top, area.bottom() - 10))
        tx, ty = self.target
        dx, dy = tx - self.fcx, ty - self.fb
        d = math.hypot(dx, dy)
        if d < 2:
            self.target = None
            self.rest = random.randint(150, 400)   # rest 5-12 seconds
            self.canvas.walking = False
            self.remember_position()
            return
        speed = 0.8                                  # slow, gentle stroll
        self.fcx += dx / d * speed
        self.fb += dy / d * speed
        self.canvas.walking = True
        self.place()

    # ---------- setup ----------
    def setup_dialog(self):
        role, ok = QInputDialog.getItem(None, "DeskPet setup", "Which pet is this PC?",
                                        ["fire (host)", "water (client)"], 0, False)
        if not ok:
            sys.exit(0)
        self.cfg["role"] = "fire" if role.startswith("fire") else "water"
        if self.cfg["role"] == "water":
            ip, ok = QInputDialog.getText(None, "DeskPet setup",
                                          "Fire PC's Tailscale IP (e.g. 100.x.y.z):",
                                          text=self.cfg["host_ip"])
            if not ok:
                sys.exit(0)
            self.cfg["host_ip"] = ip.strip()
        sec, ok = QInputDialog.getText(None, "DeskPet setup",
                                       "Shared secret (same word on both PCs):",
                                       QLineEdit.Password, self.cfg["secret"])
        if not ok or not sec.strip():
            sys.exit(0)
        self.cfg["secret"] = sec.strip()
        save_config(self.cfg)

    def reconfigure(self):
        self.link.stop()
        self.cfg["role"] = ""
        self.setup_dialog()
        self.link = Link(self.cfg)
        self.link.message.connect(self.on_peer_message)
        self.link.status.connect(self.on_status)
        self.link.start()
        self.say("Settings saved. Restart me if you changed which pet I am.")

    # ---------- speech ----------
    def say(self, text, seconds=10, compact=False):
        self.bubble.setStyleSheet(COMPACT_STYLE if compact else BUBBLE_STYLE)
        self.bubble.setFixedWidth(230 if compact else 300)
        self.bubble.setText(text)
        self.bubble.show()
        QTimer.singleShot(0, self.relayout)
        self.canvas.cheer(gentle=compact)
        self.bubble_timer.start(int(seconds * 1000))

    def hide_bubble(self):
        self.bubble.hide()
        QTimer.singleShot(0, self.relayout)

    def on_status(self, s):
        self.net_status = s
        if s in ("Connected!", "Wrong shared secret - connection refused"):
            self.say(("🔗 " if s == "Connected!" else "⚠️ ") + s, 6)

    def on_peer_message(self, text):
        who = "Water" if self.cfg["role"] == "fire" else "Fire"
        self.log(f"{who}: {text}")
        # quiet on purpose: no sound, a small bubble, and a click on it makes it go away
        self.say(f"💬 {who}: {text}", min(15, max(6, 4 + len(text) / 10)), compact=True)

    def log(self, line):
        with CHAT_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] {line}\n")

    def set_scale(self, k):
        k = round(min(2.5, max(0.5, k)), 2)
        self.cfg["scale"] = k
        save_config(self.cfg)
        self.canvas.set_scale(k)
        QTimer.singleShot(0, self.relayout)

    def nudge_scale(self, d):
        self.set_scale(self.cfg["scale"] + d)

    def set_opacity(self, o):
        o = round(min(1.0, max(0.2, o)), 2)
        self.cfg["opacity"] = o
        save_config(self.cfg)
        self.canvas.opacity = o
        self.canvas.update()

    def nudge_opacity(self, d):
        self.set_opacity(self.cfg["opacity"] + d)

    def recent_messages(self):
        if CHAT_LOG.exists():
            lines = CHAT_LOG.read_text(encoding="utf-8").strip().splitlines()[-5:]
            if lines:
                return "\n".join(lines)
        return "No messages yet."

    def water_reminder(self):
        self.say(random.choice(["Time to drink some water! 💧",
                                "Hydration check! Take a few sips 💧"]), 15)

    def set_water_interval(self, minutes):
        self.cfg["water_minutes"] = minutes
        self.water_timer.start(minutes * 60 * 1000)
        save_config(self.cfg)

    # ---------- input ----------
    def open_input(self):
        self.input.show()
        self.input.setFocus()
        QTimer.singleShot(0, self.relayout)
        self.input_timer.start(INPUT_HIDE_MS)      # hides by itself if nothing is typed

    def restart_input_timer(self, _text=""):
        if self.input.isVisible():
            self.input_timer.start(INPUT_HIDE_MS)  # still typing, so give them more time

    def close_input(self):
        self.input_timer.stop()
        self.input.clear()
        self.input.hide()
        QTimer.singleShot(0, self.relayout)

    def submit(self):
        text = self.input.text().strip()
        self.close_input()                         # box disappears as soon as Enter is pressed
        if not text:
            return
        m = re.match(r"(?:say|msg|tell)\s+(.+)", text, re.I)
        if m:
            return self.send_chat(m.group(1))
        cmd = self.command_part(text)
        if cmd is None:
            return self.send_chat(text)
        reply = self.run_command(cmd)
        if reply is None:
            self.send_chat(text)
        else:
            self.say(reply)

    def command_part(self, text):
        """Returns the command text if this was meant for the pet, else None (so it is sent as chat).
        With chat_first on, only '/command' or '<nickname> command' counts as a command."""
        if not self.cfg.get("chat_first", True):
            return text
        if text.startswith("/"):
            return text[1:].strip()
        nick, low = self.cfg["nickname"].lower(), text.lower()
        if low == nick:
            return ""
        for sep in (" ", ",", ":", "!"):
            if low.startswith(nick + sep):
                return text[len(nick) + 1:].strip(" ,:!")
        return None

    def send_chat(self, text):
        if self.link.send(text):
            self.log(f"Me: {text}")
            self.say(f"Sent: {text}", 4)
        else:
            self.say("The other pet isn't connected right now. " + self.net_status, 6)

    def run_command(self, raw):
        """Returns a reply string, or None if it isn't a command (so it gets sent as chat)."""
        t = raw.strip()
        nick = self.cfg["nickname"].lower()
        if t.lower().startswith(nick + " ") or t.lower().startswith(nick + ","):
            t = t[len(nick):].lstrip(" ,:!")
        low = t.lower()
        if not low:
            return "Yes? I'm listening!"

        if low in ("help", "?", "commands"):
            return ("Just type to chat with the other pet. For me, start with my name or a slash, "
                    "like '" + self.cfg['nickname'] + ", time' or '/time'. Commands: size 120, opacity 60, "
                    "search <text>, youtube <text>, open google/github/notepad, "
                    "remind me to <x> in 10 minutes, timer 5 minutes, water every 30 minutes, "
                    "note <text>, show notes, cpu, ram, battery, time, date, joke, status, "
                    "history, say <message>, quit")
        if low == "status":
            return f"{self.cfg['role'].title()} pet. Connection: {self.net_status}"
        if low == "history":
            if CHAT_LOG.exists():
                return "\n".join(CHAT_LOG.read_text(encoding="utf-8").strip().splitlines()[-5:])
            return "No chat history yet."

        m = re.match(r"(?:search|google)(?: for)?\s+(.+)", low)
        if m:
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(m.group(1)))
            return f"Searching Google for '{m.group(1)}' 🔍"
        m = re.match(r"youtube\s+(.+)", low)
        if m:
            webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(m.group(1)))
            return f"Looking up '{m.group(1)}' on YouTube 🎬"
        m = re.match(r"open\s+(.+)", low)
        if m:
            name = m.group(1).strip()
            if name in self.cfg["sites"]:
                webbrowser.open(self.cfg["sites"][name])
                return f"Opening {name}!"
            ok = launch(self.cfg["apps"].get(name, name))
            return f"Opening {name}!" if ok else f"I couldn't find '{name}'. Add it to apps in config.json."
        m = re.match(r"remind me to (.+?) in (\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)", low)
        if m:
            what, secs = m.group(1), to_seconds(int(m.group(2)), m.group(3))
            QTimer.singleShot(secs * 1000, lambda: self.say(f"Reminder: {what} ⏰", 20))
            return f"Okay! I'll remind you to {what} in {m.group(2)} {m.group(3)}."
        m = re.match(r"(?:set )?timer(?: for)?\s+(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)", low)
        if m:
            secs = to_seconds(int(m.group(1)), m.group(2))
            QTimer.singleShot(secs * 1000, lambda: self.say("Time's up! ⏰", 20))
            return f"Timer set for {m.group(1)} {m.group(2)}."
        m = re.match(r"(?:size|scale)\s+(\d+)\s*%?$", low)
        if m:
            self.set_scale(int(m.group(1)) / 100)
            return f"Size is now {int(self.cfg['scale'] * 100)}%."
        m = re.match(r"opacity\s+(\d+)\s*%?$", low)
        if m:
            self.set_opacity(int(m.group(1)) / 100)
            return f"Opacity is now {int(self.cfg['opacity'] * 100)}%."
        if low in ("bigger", "grow"):
            self.nudge_scale(0.15)
            return f"Size is now {int(self.cfg['scale'] * 100)}%."
        if low in ("smaller", "shrink"):
            self.nudge_scale(-0.15)
            return f"Size is now {int(self.cfg['scale'] * 100)}%."
        m = re.match(r"water every (\d+)", low)
        if m:
            self.set_water_interval(max(1, int(m.group(1))))
            return f"I'll remind you to drink water every {m.group(1)} minutes 💧"
        m = re.match(r"(?:note|remember)\s+(.+)", t, re.I)
        if m:
            with NOTES_FILE.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] {m.group(1)}\n")
            return "Saved your note 📝"
        if low in ("show notes", "notes"):
            if NOTES_FILE.exists():
                return "Your last notes:\n" + "\n".join(
                    NOTES_FILE.read_text(encoding="utf-8").strip().splitlines()[-5:])
            return "You have no notes yet."
        if low in ("time", "what time is it"):
            return f"It's {datetime.datetime.now():%I:%M %p}."
        if low in ("date", "today"):
            return f"Today is {datetime.datetime.now():%A, %d %B %Y}."
        if low == "cpu":
            return f"CPU is at {psutil.cpu_percent(interval=0.3)}%."
        if low in ("ram", "memory"):
            return f"RAM is {psutil.virtual_memory().percent}% used."
        if low == "battery":
            b = psutil.sensors_battery()
            return "No battery found." if b is None else f"Battery is at {int(b.percent)}%."
        if low == "joke":
            return random.choice(JOKES)
        m = re.match(r"(?:rename to|call you|your name is)\s+(.+)", low)
        if m:
            self.cfg["nickname"] = m.group(1).strip().title()
            save_config(self.cfg)
            return f"Call me {self.cfg['nickname']} from now on!"
        if low in ("quit", "quit pet"):
            QTimer.singleShot(1200, QApplication.quit)
            return "Bye bye! 👋"
        return None

    # ---------- window behaviour ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.moved = False

    def mouseMoveEvent(self, e):
        if self.drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag_offset)
            self.fcx = self.x() + self.width() / 2
            self.fb = self.y() + self.height()
            self.target = None
            self.moved = True

    def mouseReleaseEvent(self, e):
        was_drag = self.drag_offset is not None
        self.drag_offset = None
        if not was_drag:
            return
        if self.moved:
            self.place()                  # settle fully inside whichever monitor it was dropped on
            self.remember_position()
        elif self.bubble.isVisible() and self.bubble.geometry().contains(e.position().toPoint()):
            self.hide_bubble()            # a plain click on the speech bubble dismisses it

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if not d or not (e.modifiers() & Qt.ControlModifier):
            return                        # Ctrl + wheel = size, Ctrl + Shift + wheel = opacity
        step = 1 if d > 0 else -1
        if e.modifiers() & Qt.ShiftModifier:
            self.nudge_opacity(0.1 * step)
        else:
            self.nudge_scale(0.1 * step)

    def mouseDoubleClickEvent(self, _):
        self.open_input()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction("Type a command / message", self.open_input)
        menu.addAction("Connection status", lambda: self.say(f"Connection: {self.net_status}", 5))
        menu.addAction("Recent messages", lambda: self.say(self.recent_messages(), 15))
        menu.addAction("Drink water now 💧", self.water_reminder)
        sz = menu.addMenu("Size")
        for label, val in (("Tiny", 0.6), ("Small", 0.8), ("Normal", 1.0), ("Large", 1.3), ("Huge", 1.7)):
            a = sz.addAction(f"{label} ({int(val * 100)}%)", lambda *_, v=val: self.set_scale(v))
            a.setCheckable(True)
            a.setChecked(abs(self.cfg["scale"] - val) < 0.05)
        sz.addSeparator()
        sz.addAction("Bigger", lambda *_: self.nudge_scale(0.1))
        sz.addAction("Smaller", lambda *_: self.nudge_scale(-0.1))
        op = menu.addMenu("Opacity")
        for pct in (100, 80, 60, 40, 25):
            a = op.addAction(f"{pct}%", lambda *_, v=pct / 100: self.set_opacity(v))
            a.setCheckable(True)
            a.setChecked(abs(self.cfg["opacity"] - pct / 100) < 0.05)
        menu.addAction("Stop wandering" if self.cfg.get("wander", True) else "Wander around",
                       self.toggle_wander)
        menu.addAction("Start with Windows", lambda: self.say(self.toggle_startup(), 6))
        menu.addAction("Help", lambda *_: self.say(self.run_command("help"), 20))
        menu.addAction("Chat settings...", self.reconfigure)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.quit)
        self.menu_open = True
        menu.exec(e.globalPos())
        self.menu_open = False

    def toggle_wander(self):
        self.cfg["wander"] = not self.cfg.get("wander", True)
        save_config(self.cfg)
        self.say("Okay, I'll wander around!" if self.cfg["wander"] else "Okay, I'll stay right here.", 4)

    # ---------- start with Windows ----------
    def startup_file(self):
        return (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / "Startup" / "DeskPet.vbs")

    def install_startup(self):
        vbs = self.startup_file()
        py = Path(sys.executable)
        exe = py.with_name("pythonw.exe") if py.with_name("pythonw.exe").exists() else py
        main_py = HERE / "main.py"
        vbs.parent.mkdir(parents=True, exist_ok=True)
        # window style 0 = completely hidden, so no CMD window ever appears
        vbs.write_text(f'CreateObject("Wscript.Shell").Run """{exe}"" ""{main_py}""", 0\n', encoding="utf-8")

    def ensure_startup(self):
        """First run only: switch on 'start with Windows' automatically so the pet is there at boot."""
        if os.name != "nt" or self.cfg.get("autostart_done"):
            return
        try:
            self.install_startup()
        except Exception:
            return          # try again next launch
        self.cfg["autostart_done"] = True
        save_config(self.cfg)

    def toggle_startup(self):
        if os.name != "nt":
            return "Start with Windows only works on Windows."
        vbs = self.startup_file()
        if vbs.exists():
            vbs.unlink()
            self.cfg["autostart_done"] = True      # turned off on purpose, so don't switch it back on
            save_config(self.cfg)
            return "Okay, I won't start by myself anymore."
        self.install_startup()
        return "I'll start with Windows and stay with you 💛"


def main():
    install_crash_log()
    app = QApplication(sys.argv)
    DATA_DIR.mkdir(exist_ok=True)
    lock = QLockFile(str(DATA_DIR / "deskpet.lock"))   # only one pet at a time
    if not lock.tryLock(100):
        sys.exit(0)
    pet = PetWindow()
    pet.show()
    pet.relayout()
    for sig in (app.screenAdded, app.screenRemoved, app.primaryScreenChanged):
        sig.connect(lambda *_: QTimer.singleShot(800, pet.watchdog))   # monitors plugged or unplugged
    code = app.exec()
    lock.unlock()
    sys.exit(code)


if __name__ == "__main__":
    main()
