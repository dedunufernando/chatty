"""First-run setup for DeskPet: pair with a friend using a code, then pick an avatar and a name.

The wizard knows nothing about the pet itself. main.py hands it two small helpers:
  avatars_fn()          -> [(name, path_to_png), ...] the avatars you can choose from
  add_avatar_fn(parent) -> name of a newly added avatar, or None (asks for a picture itself)
"""
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QRadioButton, QVBoxLayout,
                               QWizard, QWizardPage)

import relay

PAGE_INTRO, PAGE_CODE, PAGE_AVATAR, PAGE_NAME = range(4)


class IntroPage(QWizardPage):
    def __init__(self, full):
        super().__init__()
        self.setTitle("Welcome! Let's connect you and your friend")
        self.setSubTitle("Two pets, one secret code. Nothing to install and no accounts to make.")
        lay = QVBoxLayout(self)
        self.start = QRadioButton("I'm going first: give me a code to share")
        self.join = QRadioButton("My friend already has a code: I'll type it in")
        self.skip = QRadioButton("Skip for now: I just want a pet on my desktop")
        self.start.setChecked(True)
        for b in (self.start, self.join) + ((self.skip,) if full else ()):
            b.setStyleSheet("font-size:15px; padding:6px;")
            lay.addWidget(b)
        note = QLabel("You can pair later from the pet's right-click menu.")
        note.setStyleSheet("color:#777; padding-top:10px;")
        lay.addWidget(note)
        lay.addStretch()

    def mode(self):
        return "join" if self.join.isChecked() else "skip" if self.skip.isChecked() else "start"


class CodePage(QWizardPage):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("font-size:14px;")
        lay.addWidget(self.info)
        self.code_label = QLabel()                     # shown when you start the pairing
        self.code_label.setAlignment(Qt.AlignCenter)
        f = QFont("Consolas", 26)
        f.setBold(True)
        self.code_label.setFont(f)
        self.code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.code_label.setStyleSheet("background:#eef6f6; border-radius:12px; padding:16px; color:#1c5c5c;")
        lay.addWidget(self.code_label)
        self.copy_btn = QPushButton("Copy the code")
        self.copy_btn.clicked.connect(self.copy_code)
        lay.addWidget(self.copy_btn, alignment=Qt.AlignCenter)
        self.edit = QLineEdit()                        # shown when you already have a code
        self.edit.setPlaceholderText("XXXX-XXXX-XXXX")
        ef = QFont("Consolas", 22)
        self.edit.setFont(ef)
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.textChanged.connect(self.reformat)
        lay.addWidget(self.edit)
        self.warn = QLabel("")
        self.warn.setStyleSheet("color:#b3402a;")
        lay.addWidget(self.warn)
        lay.addStretch()
        self.code = ""

    def initializePage(self):
        w = self.wizard()
        joining = w.mode() == "join"
        self.edit.setVisible(joining)
        self.code_label.setVisible(not joining)
        self.copy_btn.setVisible(not joining)
        if joining:
            self.setTitle("Type your friend's code")
            self.setSubTitle("Ask your friend for the 12 letters their pet showed them.")
            self.info.setText("Capital letters or small ones, with or without dashes: it all works.")
            self.edit.setFocus()
        else:
            if not self.code:
                self.code = relay.new_code()
            self.code_label.setText(self.code)
            self.setTitle("Your pairing code")
            self.setSubTitle("Share this code with your friend. Keep it between the two of you.")
            self.info.setText("Send it by message or read it out. When your friend opens their pet and "
                              "chooses \"I already have a code\", the two pets find each other by themselves.")

    def copy_code(self):
        QApplication.clipboard().setText(self.code)
        self.copy_btn.setText("Copied!")

    def reformat(self, text):
        raw = relay.clean_code(text)[:relay.CODE_LEN]
        pretty = relay.format_code(raw)
        if pretty != text:
            self.edit.blockSignals(True)
            self.edit.setText(pretty)
            self.edit.blockSignals(False)
        self.warn.setText("")
        self.completeChanged.emit()

    def isComplete(self):
        if self.wizard() and self.wizard().mode() == "join":
            return relay.valid_code(self.edit.text())
        return True

    def final_code(self):
        return relay.format_code(relay.clean_code(self.edit.text())) if self.wizard().mode() == "join" else self.code


class AvatarPage(QWizardPage):
    def __init__(self, avatars_fn, add_avatar_fn):
        super().__init__()
        self.avatars_fn, self.add_avatar_fn = avatars_fn, add_avatar_fn
        self.setTitle("Choose your avatar")
        self.setSubTitle("This is the little friend that lives on your screen. You can change it any time.")
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(84, 84))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(10)
        self.list.setMinimumHeight(190)
        self.list.currentItemChanged.connect(lambda *_: self.completeChanged.emit())
        lay.addWidget(self.list)
        add = QPushButton("Add my own picture...")
        add.clicked.connect(self.add_clicked)
        lay.addWidget(add, alignment=Qt.AlignLeft)
        self.refresh()

    def refresh(self, select=None):
        keep = select or self.chosen()
        self.list.clear()
        for name, path in self.avatars_fn():
            it = QListWidgetItem(QIcon(QPixmap(str(path))), name)
            it.setData(Qt.UserRole, name)
            it.setTextAlignment(Qt.AlignHCenter)
            self.list.addItem(it)
            if name == keep:
                self.list.setCurrentItem(it)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)

    def add_clicked(self):
        name = self.add_avatar_fn(self.wizard())
        if name:
            self.refresh(select=name)

    def chosen(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else ""

    def isComplete(self):
        return bool(self.chosen())


class NamePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("What's your pet's name?")
        self.setSubTitle("Your friend sees this name on your messages. To give the pet a command, "
                         "start with its name, like \"Bubbles, time\".")
        lay = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setMaxLength(20)
        self.edit.setStyleSheet("font-size:18px; padding:6px;")
        self.edit.textChanged.connect(lambda *_: self.completeChanged.emit())
        lay.addWidget(self.edit)
        lay.addStretch()

    def initializePage(self):
        if not self.edit.text():
            self.edit.setText(self.wizard().avatar_name() or "Pet")

    def isComplete(self):
        return bool(self.edit.text().strip())


class PairWizard(QWizard):
    def __init__(self, avatars_fn, add_avatar_fn, full=True, parent=None):
        super().__init__(parent)
        self.full = full
        self.setWindowTitle("DeskPet setup" if full else "Pair with a friend")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumSize(560, 430)
        self.intro, self.codepage = IntroPage(full), CodePage()
        self.setPage(PAGE_INTRO, self.intro)
        self.setPage(PAGE_CODE, self.codepage)
        self.avatar_page = self.name_page = None
        if full:
            self.avatar_page = AvatarPage(avatars_fn, add_avatar_fn)
            self.name_page = NamePage()
            self.setPage(PAGE_AVATAR, self.avatar_page)
            self.setPage(PAGE_NAME, self.name_page)
            self.setButtonText(QWizard.FinishButton, "Let's go!")
        else:
            self.setButtonText(QWizard.FinishButton, "Done")
        self.setStartId(PAGE_INTRO)

    def mode(self):
        return self.intro.mode()

    def avatar_name(self):
        return self.avatar_page.chosen() if self.avatar_page else ""

    def nextId(self):
        cur = self.currentId()
        if cur == PAGE_INTRO:
            if self.mode() == "skip":
                return PAGE_AVATAR if self.full else -1
            return PAGE_CODE
        if cur == PAGE_CODE:
            return PAGE_AVATAR if self.full else -1
        if cur == PAGE_AVATAR:
            return PAGE_NAME
        return -1

    def data(self):
        mode = self.mode()
        return dict(mode=mode,
                    code="" if mode == "skip" else self.codepage.final_code(),
                    avatar=self.avatar_name(),
                    nickname=self.name_page.edit.text().strip() if self.name_page else "")


def run_wizard(avatars_fn, add_avatar_fn, full=True, parent=None):
    """Shows the wizard. Returns the answers as a dict, or None if it was cancelled."""
    w = PairWizard(avatars_fn, add_avatar_fn, full, parent)
    if w.exec() != QWizard.Accepted:
        return None
    return w.data()
