"""Pairing codes, encryption and the relay link for DeskPet.

Two pets that share the same pairing code can talk to each other from anywhere, with nothing to
install or connect. Each pet only makes outgoing HTTPS requests to a small public relay (ntfy), so
no ports, no VPN and no accounts are needed.

How it stays private: the relay only ever sees a topic name that is a hash made from the code, plus
encrypted text. The code itself never leaves your PC, and without it nobody can read the messages.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"    # no 0, O, 1 or I, so codes are easy to read out
CODE_LEN = 12                                       # 12 letters = 60 bits, far too many to guess
DEFAULT_SERVER = "https://ntfy.sh"
AAD = b"deskpet1"


# ---------------------------------------------------------------- codes and crypto
def format_code(raw):
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def new_code():
    return format_code("".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN)))


def clean_code(text):
    return "".join(c for c in str(text).upper() if c in ALPHABET)


def valid_code(text):
    return len(clean_code(text)) == CODE_LEN


def derive(code):
    """From the code, work out the encryption key and the (secret-looking) relay topic name."""
    key = hashlib.scrypt(clean_code(code).encode(), salt=b"deskpet/v1", n=2 ** 14, r=8, p=1, dklen=32)
    topic = "dp-" + hmac.new(key, b"topic", hashlib.sha256).hexdigest()[:40]
    return key, topic


def seal(key, obj):
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(obj, separators=(",", ":")).encode("utf-8"), AAD)
    return "dp1." + base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")


def unseal(key, text):
    """Returns the message dict, or None if it isn't from someone holding the same code."""
    try:
        if not text.startswith("dp1."):
            return None
        raw = base64.urlsafe_b64decode(text[4:] + "=" * (-len(text[4:]) % 4))
        data = json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], AAD).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------- the link
class Relay(QObject):
    """Same job as the old direct Link, but through the relay. Works behind any router or Wi-Fi."""
    message = Signal(str)          # text from the friend (their name is in .peer_name)
    status = Signal(str)           # "Connecting...", "Connected!", "Offline - retrying..." ...
    sent = Signal(bool, str)       # (worked, text) after a send finishes
    peer_online = Signal(str)      # the friend just opened their pet

    RETRY_MIN, RETRY_MAX = 3, 60   # seconds between reconnect tries (grows, then stays at the max)
    STALL_SECONDS = 130            # the relay pings every ~45 s; silence this long means a dead link

    def __init__(self, cfg, state_file):
        super().__init__()
        self.cfg = cfg
        self.state_file = Path(state_file)
        self.nam = QNetworkAccessManager(self)
        self.reply = None
        self.buf = b""
        self.running = False
        self.peer_name = "Friend"
        self.last_activity = 0.0
        self.retry_s = self.RETRY_MIN
        self.key, self.topic = derive(cfg["code"])
        self.server = (cfg.get("relay_server") or DEFAULT_SERVER).rstrip("/")
        self.device = cfg.get("device") or secrets.token_hex(6)
        cfg["device"] = self.device
        self.state = self.load_state()
        self.seen = deque(self.state.get("seen", []), maxlen=300)
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self.connect_stream)
        self.watch = QTimer(self)
        self.watch.timeout.connect(self.check_stall)
        self.last_peer_note = 0.0

    # ----- saved position in the conversation, so nothing repeats after a restart
    def load_state(self):
        try:
            st = json.loads(self.state_file.read_text(encoding="utf-8"))
            if st.get("topic") == self.topic:
                return st
        except Exception:
            pass
        return {"topic": self.topic, "last_time": int(time.time()) - 5, "seen": []}

    def save_state(self):
        self.state["seen"] = list(self.seen)
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self.state), encoding="utf-8")
        except Exception:
            pass

    # ----- start / stop
    def start(self):
        self.running = True
        self.watch.start(20000)
        self.connect_stream()

    def stop(self):
        self.running = False
        self.retry_timer.stop()
        self.watch.stop()
        if self.reply is not None:
            r, self.reply = self.reply, None
            r.abort()
            r.deleteLater()

    # ----- receiving
    def connect_stream(self):
        if not self.running or self.reply is not None:
            return
        since = max(0, int(self.state.get("last_time", time.time())) - 2)   # a little overlap, duplicates are filtered
        req = QNetworkRequest(QUrl(f"{self.server}/{self.topic}/json?since={since}"))
        if hasattr(req, "setTransferTimeout"):
            req.setTransferTimeout(0)                   # a long-lived stream must never time out by itself
        req.setRawHeader(b"Accept", b"application/x-ndjson")
        self.buf = b""
        self.last_activity = time.time()
        self.reply = self.nam.get(req)
        reply = self.reply
        reply.readyRead.connect(lambda: self.on_ready(reply))
        reply.finished.connect(lambda: self.on_finished(reply))
        self.status.emit("Connecting...")

    def on_ready(self, reply):
        if reply is not self.reply:
            return
        self.last_activity = time.time()
        self.buf += bytes(reply.readAll())
        if len(self.buf) > 1_000_000:
            reply.abort()
            return
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            kind = ev.get("event")
            if kind == "open":
                self.retry_s = self.RETRY_MIN
                self.status.emit("Connected!")
                self.announce()
            elif kind == "message":
                self.handle(ev)

    def on_finished(self, reply):
        if reply is not self.reply:
            reply.deleteLater()
            return
        self.reply = None
        code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        reply.deleteLater()
        if not self.running:
            return
        if code in (401, 403):
            self.status.emit("The relay said no (HTTP %s)" % code)
        elif code == 429:
            self.status.emit("Relay is busy, trying again soon...")
        else:
            self.status.emit("Offline - retrying...")
        self.retry_timer.start(int(self.retry_s * 1000))
        self.retry_s = min(self.RETRY_MAX, self.retry_s * 2)

    def check_stall(self):
        if self.reply is not None and time.time() - self.last_activity > self.STALL_SECONDS:
            self.reply.abort()                          # on_finished then reconnects

    def handle(self, ev):
        mid = ev.get("id")
        if not mid or mid in self.seen:
            return
        self.seen.append(mid)
        self.state["last_time"] = max(int(self.state.get("last_time", 0)), int(ev.get("time", 0)))
        self.save_state()
        data = unseal(self.key, str(ev.get("message", "")))
        if not data or data.get("d") == self.device:
            return                                      # not ours to read, or our own echo
        kind = data.get("k")
        name = str(data.get("n") or "Friend")[:30]
        fresh = time.time() - int(ev.get("time", 0)) < 120
        if kind == "m":
            self.peer_name = name
            self.message.emit(str(data.get("t", ""))[:500])
        elif kind in ("hi", "hi2") and fresh:
            self.peer_name = name
            if kind == "hi":
                self.publish({"k": "hi2"})              # tell them we are here too
            if time.time() - self.last_peer_note > 600:
                self.last_peer_note = time.time()
                self.peer_online.emit(name)

    # ----- sending
    def publish(self, payload, text=None):
        body = dict(payload)
        body.update(d=self.device, n=self.cfg.get("nickname") or "Friend", ts=int(time.time()))
        req = QNetworkRequest(QUrl(f"{self.server}/{self.topic}"))
        req.setHeader(QNetworkRequest.ContentTypeHeader, "text/plain; charset=utf-8")
        reply = self.nam.post(req, seal(self.key, body).encode("ascii"))

        def done():
            ok = reply.error() == QNetworkReply.NoError
            reply.deleteLater()
            if text is not None:
                self.sent.emit(ok, text)
        reply.finished.connect(done)

    def announce(self):
        if time.time() - float(self.state.get("last_hello", 0)) > 600:
            self.state["last_hello"] = time.time()
            self.save_state()
            self.publish({"k": "hi"})

    def send(self, text):
        if not self.running:
            return False
        self.publish({"k": "m", "t": text[:500]}, text)
        return True
