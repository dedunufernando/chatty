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
import struct
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
from PySide6.QtCore import (Qt, QTimer, QObject, Signal, QRectF, QPointF, QPoint, QSize, QLockFile, QUrl,
                            QBuffer, QByteArray, QIODevice, QStandardPaths)
from PySide6.QtNetwork import QNetworkProxyFactory
from PySide6.QtGui import (QPainter, QPixmap, QColor, QImage, QRadialGradient,
                           QLinearGradient, QBrush, QPen, QPainterPath, QDesktopServices, QIcon)
from PySide6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress, QAbstractSocket
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit,
                               QMenu, QInputDialog, QFileDialog, QMessageBox)

FROZEN = getattr(sys, "frozen", False)           # True when running as the packaged DeskPet.exe
HERE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
ASSETS = Path(getattr(sys, "_MEIPASS", HERE)) / "assets"     # built-in artwork
DATA_DIR = Path.home() / ".deskpet"
AVATARS_DIR = DATA_DIR / "avatars"             # avatars you add yourself live here
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
    "avatar": "",            # name of the avatar in use ("" = Fire or Water, depending on the pet)
    "effects": {},           # per-avatar effect: bubbles, sparks, hearts or none
    "scale": 1.0,            # avatar size: 0.5 to 2.5 (1.0 = normal)
    "opacity": 1.0,          # avatar opacity: 0.2 to 1.0
    "chat_first": True,      # True: typing sends a chat message unless you say my name or start with /
    "pos": None,             # last resting place [x, y], so the pet comes back where you left it
    "welcome_text": "",      # shown once on first launch (used by the gift package)
    "welcomed": False,
    "code": "",              # pairing code shared with your friend (set by the setup wizard)
    "relay_server": "",      # leave empty for the default public relay (ntfy.sh)
    "device": "",            # random id of this PC, so a pet never reads its own messages
    "standalone": False,     # True if you chose "skip" at setup (a pet with no friend)
    "code_declined": False,  # True after Unpair, so a gift code isn't re-applied
    "shortcut_done": False,   # set after the desktop and Start menu shortcuts were made once
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
    if src is CONFIG_FILE and gift.exists() and not cfg.get("code") and not cfg.get("code_declined"):
        try:                                   # a gift update that brings a pairing code upgrades an older install
            gcode = json.loads(gift.read_text(encoding="utf-8")).get("code", "")
            if relay.valid_code(gcode):
                cfg["code"] = relay.format_code(relay.clean_code(gcode))
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
import relay
import wizard

PET_HEIGHT = 92
PET_MAX_WIDTH = 130
EFFECTS = {"bubbles": "Bubbles 🫧", "sparks": "Sparks 🔥", "hearts": "Hearts 💗", "none": "None"}
BUILTIN_EFFECT = {"Fire": "sparks", "Water": "bubbles"}
IMAGE_FILTER = "Pictures (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def list_avatars():
    """[(name, path, builtin)] - the two built-in pets first, then the ones you added."""
    items = []
    for name in ("Fire", "Water"):
        f = ASSETS / f"{name.lower()}.png"
        if f.exists():
            items.append((name, f, True))
    if AVATARS_DIR.exists():
        for f in sorted(AVATARS_DIR.glob("*.png"), key=lambda x: x.name.lower()):
            items.append((f.stem, f, False))
    return items


def safe_name(name):
    name = re.sub(r"[^\w \-]", "", name, flags=re.UNICODE).strip()[:30]
    return name or "My avatar"


def import_avatar(path, parent=None):
    """Prepare a picture and save it as a new avatar. Returns (name, note). name is None if nothing was added;
    note is then a message for the user (empty when they simply cancelled)."""
    img = QImage(str(path))
    if img.isNull():
        return None, "I couldn't open that picture. Try a PNG or JPG."
    img, note = prepare_avatar(img)
    name, ok = QInputDialog.getText(parent, "New avatar", "Give your avatar a name:", text=Path(path).stem[:30])
    if not ok:
        return None, ""
    name = safe_name(name)
    taken = {n for n, _, _ in list_avatars()}
    base, i = name, 2
    while name in taken:                       # never overwrite an existing avatar
        name, i = f"{base} {i}", i + 1
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    img.save(str(AVATARS_DIR / f"{name}.png"), "PNG")
    return name, note


def pick_and_import_avatar(parent=None):
    path, _ = QFileDialog.getOpenFileName(parent, "Choose a picture for your new avatar",
                                          str(Path.home() / "Pictures"), IMAGE_FILTER)
    if not path:
        return None
    name, note = import_avatar(path, parent)
    if not name and note:
        QMessageBox.information(parent, "Avatar", note)
    return name


class OfflineLink(QObject):
    """Stands in for the link when the pet is used on its own, without a friend."""
    message = Signal(str)
    status = Signal(str)

    def start(self):
        pass

    def stop(self):
        pass

    def send(self, text):
        return False


def write_ico(src, out):
    """Turn a picture into a Windows .ico (16 to 256 px) using only Qt, so no extra packages are needed."""
    base = QImage(str(src)).convertToFormat(QImage.Format_ARGB32)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs = []
    for n in sizes:
        tile = QImage(n, n, QImage.Format_ARGB32)
        tile.fill(0)
        inner = max(1, int(n * 0.94))
        art = base.scaled(inner, inner, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        q = QPainter(tile)
        q.drawImage((n - art.width()) // 2, (n - art.height()) // 2, art)
        q.end()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        tile.save(buf, "PNG")
        blobs.append(bytes(ba))
    data = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for n, b in zip(sizes, blobs):
        data += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(b), offset)
        offset += len(b)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(data + b"".join(blobs))


def shortcut_script(links, target, args, workdir, icon):
    """PowerShell that creates one .lnk shortcut for each path in links."""
    q = lambda v: str(v).replace("'", "''")
    parts = ["$w=New-Object -ComObject WScript.Shell"]
    for lnk in links:
        parts.append(f"$s=$w.CreateShortcut('{q(lnk)}');$s.TargetPath='{q(target)}';$s.Arguments='{q(args)}';"
                     f"$s.WorkingDirectory='{q(workdir)}';$s.IconLocation='{q(icon)},0';"
                     f"$s.Description='DeskPet';$s.Save()")
    return ";".join(parts)


def remove_background(img, tol=40):
    """Make a plain-coloured background transparent by flood-filling in from the picture's edges.
    Returns (image, ok). ok is False when it would have eaten most of the picture."""
    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    buf = bytearray(img.constBits())          # B, G, R, A for each pixel
    def px(x, y):
        i = (y * w + x) * 4
        return buf[i], buf[i + 1], buf[i + 2]
    corners = [px(0, 0), px(w - 1, 0), px(0, h - 1), px(w - 1, h - 1)]
    ref = tuple(sorted(c[k] for c in corners)[1] for k in range(3))   # typical corner colour
    def near(x, y):
        b, g, r = px(x, y)
        return max(abs(b - ref[0]), abs(g - ref[1]), abs(r - ref[2])) <= tol
    seen = bytearray(w * h)
    stack = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] \
        + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
    removed = 0
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= w or y >= h or seen[y * w + x] or not near(x, y):
            continue
        seen[y * w + x] = 1
        removed += 1
        buf[(y * w + x) * 4 + 3] = 0
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    if removed > 0.92 * w * h:
        return img, False
    for y in range(1, h - 1):                  # soften the cut edge a little so it isn't jagged
        for x in range(1, w - 1):
            i = (y * w + x) * 4
            if buf[i + 3] and not seen[y * w + x] and (
                    seen[y * w + x - 1] or seen[y * w + x + 1] or seen[(y - 1) * w + x] or seen[(y + 1) * w + x]):
                buf[i + 3] = 110
    out = QImage(bytes(buf), w, h, w * 4, QImage.Format_ARGB32).copy()
    return out, True


def prepare_avatar(img):
    """Shrink a picture, remove a plain background if it has none, and trim the empty edges.
    Returns (image, note) where note is a hint to show the user, or ''."""
    img = img.convertToFormat(QImage.Format_ARGB32)
    if max(img.width(), img.height()) > 512:
        img = img.scaled(512, 512, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    w, h = img.width(), img.height()
    has_alpha = sum(1 for (x, y) in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
                    if img.pixelColor(x, y).alpha() < 200) >= 3
    note = ""
    if not has_alpha:
        img, ok = remove_background(img)
        if not ok:
            note = ("I couldn't cleanly remove the background. A picture with a plain background, "
                    "or a PNG that is already see-through, works best.")
    # trim transparent margins
    left, top, right, bottom = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() > 20:
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
    if right >= left and bottom >= top:
        pad = 4
        img = img.copy(max(0, left - pad), max(0, top - pad),
                       min(w, right + pad + 1) - max(0, left - pad), min(h, bottom + pad + 1) - max(0, top - pad))
    return img, note



def make_pet_pixmap(path, role, height=PET_HEIGHT, ratio=1.0):
    """Load the artwork, shrink it, and bake in soft shading so the flat art looks 3D-ish."""
    pm = QPixmap(str(path)) if path else QPixmap()
    if pm.isNull():
        pm = QPixmap(150, 150)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setBrush(QColor("#ff8800" if role == "fire" else "#3cb4b4"))
        q.drawEllipse(5, 5, 140, 140)
        q.end()
    pm = pm.scaled(QSize(max(8, int(PET_MAX_WIDTH * ratio)), max(8, int(height * ratio))),
                   Qt.KeepAspectRatio, Qt.SmoothTransformation)      # fits any shape of picture
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

    def __init__(self, role, path, effect, scale=1.0, opacity=1.0):
        super().__init__()
        self.role = role
        self.path = path
        self.effect = effect
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
        self.pm = make_pet_pixmap(self.path, self.role, PET_HEIGHT, ratio)   # logical height stays PET_HEIGHT
        self.update()

    def set_avatar(self, path, effect):
        self.path = path
        self.effect = effect
        self.particles = []
        self.set_scale(self.k)

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
            pt["x"] += pt["vx"] + (0.25 * math.sin(pt["ph"] + self.t * 0.1) if self.effect in ("bubbles", "hearts") else 0)
        self.particles = [pt for pt in self.particles if pt["life"] > 0]
        self.update()

    def spawn(self):
        if len(self.particles) > 25:
            return
        lift, _ = self.lift()
        top = self.H - 24 - lift - self.pm.height() / self.pm.devicePixelRatio()
        cx = self.W / 2
        if self.effect == "hearts" and self.t % 14 == 0:
            life = random.randint(70, 100)
            self.particles.append(dict(x=cx + random.uniform(-26, 26), y=top + 20, vx=0, vy=-0.4,
                                       size=random.uniform(3.5, 6), life=life, max=life,
                                       ph=random.uniform(0, 6)))
        elif self.effect == "bubbles" and self.t % 9 == 0:
            life = random.randint(60, 90)
            self.particles.append(dict(x=cx + random.uniform(-22, 22), y=top + 14, vx=0, vy=-0.45,
                                       size=random.uniform(2.5, 5), life=life, max=life,
                                       ph=random.uniform(0, 6)))
        elif self.effect == "sparks" and self.t % 4 == 0:
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
            r = pt["size"] * (0.4 + 0.6 * a if self.effect == "sparks" else 1)
            if self.effect == "hearts":
                c, rr = pt["x"], r
                path = QPainterPath()
                path.moveTo(c, pt["y"] + rr * 0.9)
                path.cubicTo(c - rr * 1.6, pt["y"] - rr * 0.2, c - rr * 0.6, pt["y"] - rr * 1.3, c, pt["y"] - rr * 0.4)
                path.cubicTo(c + rr * 0.6, pt["y"] - rr * 1.3, c + rr * 1.6, pt["y"] - rr * 0.2, c, pt["y"] + rr * 0.9)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 110, 150, int(230 * a)))
                p.drawPath(path)
            elif self.effect == "bubbles":
                p.setPen(QPen(QColor(255, 255, 255, int(200 * a)), 1))
                p.setBrush(QColor(170, 235, 235, int(80 * a)))
                p.drawEllipse(QPointF(pt["x"], pt["y"]), r, r)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, int(220 * a)))
                p.drawEllipse(QPointF(pt["x"] - r * 0.35, pt["y"] - r * 0.35), r * 0.25, r * 0.25)
            elif self.effect == "sparks":
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
        if not self.cfg["code"] and not (self.cfg["role"] and self.cfg["secret"]) and not self.cfg["standalone"]:
            self.first_run()
        if not self.cfg["nickname"]:
            self.cfg["nickname"] = NICK_DEFAULT.get(self.cfg["role"]) or self.default_avatar()
            save_config(self.cfg)
        self._said_connected = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAcceptDrops(True)          # drop a picture onto the pet to make it a new avatar
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

        path, effect = self.avatar_info()
        self.canvas = PetCanvas(self.cfg["role"], path, effect, self.cfg["scale"], self.cfg["opacity"])
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

        self.link = self.make_link()
        self.link.start()

        wt = self.cfg.get("welcome_text")
        if wt and not self.cfg.get("welcomed"):
            self.cfg["welcomed"] = True
            save_config(self.cfg)
            self.say(wt, 30)
        else:
            self.say(f"Hi! I'm {self.cfg['nickname']}. Double-click me to type, or say 'help'.")

        QApplication.setWindowIcon(QIcon(self.canvas.pm))
        self.ensure_startup()
        QTimer.singleShot(4000, self.ensure_shortcuts)

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
        """Old direct (Tailscale) settings. New pets use pairing codes instead."""
        self.link.stop()
        self.cfg["role"] = ""
        self.setup_dialog()
        self.swap_link()
        self.say("Settings saved. Restart me if you changed which pet I am.")

    # ---------- pairing ----------
    def make_link(self):
        if self.cfg.get("code"):
            link = relay.Relay(self.cfg, DATA_DIR / "relay_state.json")
            link.sent.connect(self.on_sent)
            link.peer_online.connect(self.on_peer_online)
        elif self.cfg["role"] and self.cfg["secret"]:
            link = Link(self.cfg)
        else:
            link = OfflineLink()
        link.message.connect(self.on_peer_message)
        link.status.connect(self.on_status)
        return link

    def swap_link(self):
        self.link.stop()
        self.link.deleteLater()
        self._said_connected = False
        self.link = self.make_link()
        self.link.start()

    def wizard_avatars(self):
        return [(n, f) for n, f, _ in list_avatars()]

    def first_run(self):
        res = wizard.run_wizard(self.wizard_avatars, pick_and_import_avatar, full=True)
        if res is None:
            sys.exit(0)
        self.cfg["code"] = res["code"]
        self.cfg["standalone"] = res["mode"] == "skip"
        self.cfg["avatar"] = res["avatar"]
        self.cfg["nickname"] = res["nickname"]
        save_config(self.cfg)

    def pair_dialog(self):
        self.menu_open = True
        try:
            res = wizard.run_wizard(self.wizard_avatars, pick_and_import_avatar, full=False)
        finally:
            self.menu_open = False
        if res is None:
            return
        self.cfg.update(code=res["code"], standalone=False, code_declined=False)
        save_config(self.cfg)
        self.swap_link()
        if res["mode"] == "start":
            QApplication.clipboard().setText(res["code"])
            self.say("Code copied! Send it to your friend. I'll tell you when they're here 💛", 12)
        else:
            self.say("Paired! Say hi to your friend 💛", 8)

    def show_code(self):
        code = self.cfg.get("code", "")
        QApplication.clipboard().setText(code)
        self.say(f"Your pairing code is {code} (copied). Only share it with your friend.", 15)

    def unpair(self):
        self.menu_open = True
        try:
            sure = QMessageBox.question(None, "Unpair", "Stop chatting with your friend's pet?\n"
                                        "You can pair again any time with a code.")
        finally:
            self.menu_open = False
        if sure != QMessageBox.Yes:
            return
        self.cfg.update(code="", code_declined=True, standalone=True)
        save_config(self.cfg)
        try:
            (DATA_DIR / "relay_state.json").unlink()
        except OSError:
            pass
        self.swap_link()
        self.say("Unpaired. I'm all yours now.", 6)

    def on_sent(self, ok, text):
        if ok:
            self.log(f"Me: {text}")
            self.say(f"Sent: {text}", 4)
        else:
            self.say("I couldn't send that. Check the internet connection and try again.", 6)

    def on_peer_online(self, name):
        self.say(f"💛 {name} is here", 5, compact=True)

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
        if s == "Connected!" and not self._said_connected:
            self._said_connected = True          # once per launch, so reconnecting never nags
            self.say("🔗 " + s, 4)
        elif s.startswith("Wrong shared secret") or s.startswith("The relay said no"):
            self.say("⚠️ " + s, 6)

    def on_peer_message(self, text):
        if isinstance(self.link, relay.Relay):
            who = self.link.peer_name
        else:
            who = "Water" if self.cfg["role"] == "fire" else "Fire"
        self.log(f"{who}: {text}")
        # quiet on purpose: no sound, a small bubble, and a click on it makes it go away
        self.say(f"💬 {who}: {text}", min(15, max(6, 4 + len(text) / 10)), compact=True)

    def log(self, line):
        with CHAT_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] {line}\n")

    # ---------- avatars ----------
    def default_avatar(self):
        return "Fire" if self.cfg["role"] == "fire" else "Water"

    def avatar_info(self):
        """(image path, effect) for the avatar in use, falling back to the built-in pet."""
        avatars = {n: f for n, f, _ in list_avatars()}
        name = self.cfg.get("avatar") or self.default_avatar()
        if name not in avatars:
            name = self.default_avatar()
        effect = self.cfg.get("effects", {}).get(name) or BUILTIN_EFFECT.get(name, "none")
        return avatars.get(name), effect

    def use_avatar(self, name):
        self.cfg["avatar"] = name
        save_config(self.cfg)
        path, effect = self.avatar_info()
        self.canvas.set_avatar(path, effect)
        QApplication.setWindowIcon(QIcon(self.canvas.pm))
        QTimer.singleShot(0, self.relayout)
        self.say(f"Now I'm {name}!", 4)

    def set_effect(self, effect):
        name = self.cfg.get("avatar") or self.default_avatar()
        self.cfg.setdefault("effects", {})[name] = effect
        save_config(self.cfg)
        self.canvas.effect = effect
        self.canvas.particles = []

    def add_avatar_dialog(self):
        self.menu_open = True                  # keeps the pet still while a window is open
        try:
            path, _ = QFileDialog.getOpenFileName(None, "Choose a picture for your new avatar",
                                                  str(Path.home() / "Pictures"), IMAGE_FILTER)
        finally:
            self.menu_open = False
        if path:
            self.add_avatar(path)

    def add_avatar(self, path):
        self.menu_open = True                  # keeps the pet still while a window is open
        try:
            if QImage(str(path)).isNull():
                return self.say("I couldn't open that picture. Try a PNG or JPG.", 6)
            self.say("One moment, getting your avatar ready...", 4)
            QApplication.processEvents()
            name, note = import_avatar(path)
        finally:
            self.menu_open = False
        if not name:
            if note:
                self.say(note, 6)
            return
        self.cfg.setdefault("effects", {})[name] = "none"
        self.use_avatar(name)
        if note:
            QTimer.singleShot(4500, lambda: self.say(note, 12))

    def delete_avatar(self):
        name = self.cfg.get("avatar") or self.default_avatar()
        mine = {n: f for n, f, builtin in list_avatars() if not builtin}
        if name not in mine:
            return self.say("The built-in pets can't be deleted.", 4)
        self.menu_open = True
        try:
            sure = QMessageBox.question(None, "Delete avatar", f"Delete the avatar '{name}'?")
        finally:
            self.menu_open = False
        if sure == QMessageBox.Yes:
            mine[name].unlink(missing_ok=True)
            self.cfg.get("effects", {}).pop(name, None)
            self.use_avatar(self.default_avatar())

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(IMAGE_EXT) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(IMAGE_EXT):
                self.add_avatar(u.toLocalFile())
                break

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
        if isinstance(self.link, relay.Relay):
            if not self.link.send(text):
                self.say("I'm not connected yet. " + self.net_status, 6)
            return                               # the result arrives in on_sent
        if isinstance(self.link, OfflineLink):
            return self.say("I'm not paired with a friend yet. Right-click me and choose Pair with a friend.", 8)
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
            mode = "paired by code" if self.cfg.get("code") else "direct" if self.cfg["role"] else "on my own"
            return f"{self.cfg['nickname']} ({mode}). Connection: {self.net_status}"
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
        av = menu.addMenu("Avatar")
        current = self.cfg.get("avatar") or self.default_avatar()
        for name, _f, builtin in list_avatars():
            a = av.addAction(name, lambda *_, n=name: self.use_avatar(n))
            a.setCheckable(True)
            a.setChecked(name == current)
        av.addSeparator()
        av.addAction("Add avatar from a picture...", lambda *_: self.add_avatar_dialog())
        fx = av.addMenu("Effect around this avatar")
        cur_fx = self.avatar_info()[1]
        for key, label in EFFECTS.items():
            a = fx.addAction(label, lambda *_, k=key: self.set_effect(k))
            a.setCheckable(True)
            a.setChecked(key == cur_fx)
        av.addAction("Delete this avatar", lambda *_: self.delete_avatar())
        av.addAction("Open my avatars folder", lambda *_: (AVATARS_DIR.mkdir(parents=True, exist_ok=True),
                                                           QDesktopServices.openUrl(QUrl.fromLocalFile(str(AVATARS_DIR)))))
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
        menu.addAction("Add shortcut icon to Desktop", lambda *_: self.say(self.make_shortcuts(), 6))
        menu.addAction("Help", lambda *_: self.say(self.run_command("help"), 20))
        if self.cfg.get("code"):
            pm = menu.addMenu("Friend pairing")
            pm.addAction("Show my pairing code", lambda *_: self.show_code())
            pm.addAction("Pair with someone else...", lambda *_: self.pair_dialog())
            pm.addAction("Unpair", lambda *_: self.unpair())
        else:
            menu.addAction("Pair with a friend...", lambda *_: self.pair_dialog())
            if self.cfg["role"] and self.cfg["secret"]:
                menu.addAction("Direct (Tailscale) settings...", self.reconfigure)
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
        vbs.parent.mkdir(parents=True, exist_ok=True)
        # window style 0 = completely hidden, so no CMD window ever appears
        if FROZEN:
            vbs.write_text(f'CreateObject("Wscript.Shell").Run """{py}""", 0\n', encoding="utf-8")
        else:
            exe = py.with_name("pythonw.exe") if py.with_name("pythonw.exe").exists() else py
            main_py = HERE / "main.py"
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

    # ---------- desktop + Start menu shortcuts ----------
    def make_shortcuts(self):
        """Shortcuts on the Desktop and in the Start menu, with the current avatar as the icon."""
        if os.name != "nt":
            return "Shortcuts only work on Windows."
        name = self.cfg.get("avatar") or self.default_avatar()
        src = self.avatar_info()[0]
        try:
            ico = DATA_DIR / "icons" / f"{safe_name(name)}.ico"
            write_ico(src, ico)
            where = (QStandardPaths.DesktopLocation, QStandardPaths.ApplicationsLocation)
            links = []
            for loc in where:
                folder = Path(QStandardPaths.writableLocation(loc))
                folder.mkdir(parents=True, exist_ok=True)
                links.append(folder / "DeskPet.lnk")
            py = Path(sys.executable)
            if FROZEN:
                target, args = py, ""
            else:
                target = py.with_name("pythonw.exe") if py.with_name("pythonw.exe").exists() else py
                args = f'"{HERE / "main.py"}"'
            script = shortcut_script(links, target, args, HERE, ico)
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                             creationflags=0x08000000)          # no console window flashes up
        except Exception:
            return "I couldn't make the shortcut this time."
        return "Done! I'm on your desktop and in the Start menu now 💛"

    def ensure_shortcuts(self):
        """First run only: put an icon on the Desktop and in the Start menu."""
        if os.name != "nt" or self.cfg.get("shortcut_done"):
            return
        self.make_shortcuts()
        self.cfg["shortcut_done"] = True       # only once, so a shortcut you delete stays deleted
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
    QNetworkProxyFactory.setUseSystemConfiguration(True)
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
