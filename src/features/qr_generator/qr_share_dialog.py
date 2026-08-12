"""
qr_share_dialog.py — Floating QR-share popup.

When the user right-clicks on selected text and chooses "Share as QR…",
a compact, draggable, theme-aware popup opens showing the QR code, the
encoded text, and quick-action buttons. The QR is *not* stamped onto
the PDF — this matches MS Edge's "Share as QR" UX where the code is a
transient overlay rather than a permanent page edit.

Design notes
------------
The popup uses the platform's standard window frame (close / minimize /
maximize / system menu) per user request. The hero-sized QR has plenty
of breathing room and the accent color is sourced from the active theme
so the dialog looks at home in both dark and light palettes.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QImage, QGuiApplication, QColor, QPainter, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QFrame, QSizePolicy, QGraphicsDropShadowEffect, QApplication,
    QScrollArea,
)


# Accent colors match the dark/light theme palettes used elsewhere.
_ACCENT_LIGHT = "#0067c0"   # Edge blue
_ACCENT_DARK  = "#89b4fa"   # Catppuccin mocha blue
_CARD_BG_LIGHT = "#ffffff"
_CARD_BG_DARK  = "#1e2030"
_TEXT_LIGHT    = "#1f1f1f"
_TEXT_DARK     = "#cdd6f4"
_SUBTLE_LIGHT  = "#6b6b6b"
_SUBTLE_DARK   = "#9399b2"
_BORDER_LIGHT  = "#e0e0e0"
_BORDER_DARK   = "#313244"


def _is_dark() -> bool:
    """Cheap heuristic: are we in the dark theme?"""
    pal = QApplication.instance().palette() if QApplication.instance() else None
    if pal is None:
        return True
    bg = pal.color(pal.ColorRole.Window)
    return bg.lightness() < 128


class QRShareDialog(QDialog):
    """Compact, draggable, non-modal QR popup."""

    # The QR image is rendered at this size at minimum (the actual PNG
    # is 900 px from render_png(), so the label is never upscaled — it
    # just guarantees the on-screen cell is always roomy).
    QR_MIN_PX = 380

    # ------------------------------------------------------------------
    def __init__(self, png_bytes: bytes, text: str, parent=None,
                 truncated: bool = False, original_length: int = 0,
                 encoded_length: int = 0):
        # Use the default dialog window type so the platform's native
        # title bar with close / minimize / maximize / system-menu
        # buttons is shown. The user explicitly asked for a standard
        # window frame (no FramelessWindowHint, no Tool flag).
        super().__init__(parent)
        self._png_bytes = png_bytes
        self._text = text
        self._truncated = truncated
        self._original_length = original_length or len(text)
        self._encoded_length = encoded_length or len(text)
        self.setWindowTitle("Share as QR")
        self.setModal(False)
        # Explicitly request standard buttons so they're visible on
        # platforms that don't auto-add them (some Linux WMs).
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.setMinimumSize(720, 720)
        self.resize(820, 880)
        self._build_ui()
        self._apply_theme()
        # No pop-in animation — instant cut, matching the no-animation
        # policy on the rest of the viewer (per user request).

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # Standard native window frame is provided by the platform —
        # no custom title bar needed. We use a single root layout that
        # hosts a QScrollArea so all content remains reachable even on
        # small displays / tight title bar regions.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Drop shadow gives the dialog a "floating" feel under the
        # native frame.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 80))
        self.setGraphicsEffect(shadow)

        # Card frame with rounded corners holds everything.
        self._card = QFrame(self)
        self._card.setObjectName("qrShareCard")
        root.addWidget(self._card)
        card = QVBoxLayout(self._card)
        card.setContentsMargins(0, 0, 0, 0)
        card.setSpacing(0)

        # ---- Hero section: big QR with a soft background ----
        hero = QFrame()
        hero.setObjectName("qrShareHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(40, 24, 40, 18)
        hero_layout.setSpacing(0)

        img = QImage.fromData(self._png_bytes)
        if img.isNull():
            qr_label = QLabel("(failed to render QR)")
            qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            # Scale up to a comfortable scan size — chosen so the whole
            # dialog fits on a typical 1080p screen without overflow.
            target_px = self.QR_MIN_PX
            if img.width() < target_px or img.height() < target_px:
                img = img.scaled(
                    target_px, target_px,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            pix = QPixmap.fromImage(img)
            self._qr_pixmap = pix
            qr_label = QLabel()
            qr_label.setPixmap(pix)
            qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            qr_label.setMinimumSize(target_px, target_px)
            qr_label.setMaximumSize(target_px + 80, target_px + 80)
        self._qr_label = qr_label
        qr_label.setObjectName("qrShareImage")
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(qr_label, stretch=0, alignment=Qt.AlignmentFlag.AlignCenter)

        # Subtle hint below the QR
        hint = QLabel("Scan with your phone camera")
        hint.setObjectName("qrShareHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setContentsMargins(0, 10, 0, 0)
        hero_layout.addWidget(hint)

        card.addWidget(hero)

        # ---- Encoded text section (read-only, monospaced) ----
        text_section = QFrame()
        text_section.setObjectName("qrShareTextSection")
        text_layout = QVBoxLayout(text_section)
        text_layout.setContentsMargins(20, 12, 20, 12)
        text_layout.setSpacing(4)

        meta = QLabel(
            f"Encoded text · {len(self._text)} chars · "
            f"{len(self._text.encode('utf-8'))} bytes")
        meta.setObjectName("qrShareMeta")
        text_layout.addWidget(meta)

        # Truncation banner — shown when the selection was too long
        # for a single QR (QRv40 caps at ~1273 byte binary at Q error).
        # Without this the dialog silently encodes less than the user
        # selected and the recipient's QR scanner reads a truncated
        # string, which is confusing.
        if self._truncated:
            warn = QLabel(
                f"⚠ Selection was truncated: encoded "
                f"{self._encoded_length} of {self._original_length} "
                f"chars (QR limit reached). Use Copy text for the full "
                f"selection.")
            warn.setObjectName("qrShareWarn")
            warn.setWordWrap(True)
            text_layout.addWidget(warn)

        self._text_view = QTextEdit()
        self._text_view.setReadOnly(True)
        self._text_view.setPlainText(self._text)
        self._text_view.setObjectName("qrShareText")
        # More headroom for long selections — 8-line minimum, 220px max.
        self._text_view.setMinimumHeight(120)
        self._text_view.setMaximumHeight(220)
        monospace = QFont("JetBrains Mono")
        if not monospace.exactMatch():
            monospace = QFont("Consolas")
        monospace.setStyleHint(QFont.StyleHint.Monospace)
        monospace.setPointSize(10)
        self._text_view.setFont(monospace)
        text_layout.addWidget(self._text_view)

        card.addWidget(text_section)

        # ---- Action row ----
        actions = QFrame()
        actions.setObjectName("qrShareActions")
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(16, 12, 16, 14)
        actions_layout.setSpacing(8)

        copy_text_btn = QPushButton("Copy text")
        copy_text_btn.setObjectName("qrSharePrimaryBtn")
        copy_text_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_text_btn.clicked.connect(self._copy_text)
        actions_layout.addWidget(copy_text_btn)

        copy_img_btn = QPushButton("Copy image")
        copy_img_btn.setObjectName("qrShareSecondaryBtn")
        copy_img_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_img_btn.clicked.connect(self._copy_image)
        actions_layout.addWidget(copy_img_btn)

        save_btn = QPushButton("Save PNG")
        save_btn.setObjectName("qrShareSecondaryBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save_png)
        actions_layout.addWidget(save_btn)

        actions_layout.addStretch(1)

        card.addWidget(actions)

    # ------------------------------------------------------------------ theme
    def _apply_theme(self):
        dark = _is_dark()
        accent = _ACCENT_DARK if dark else _ACCENT_LIGHT
        bg = _CARD_BG_DARK if dark else _CARD_BG_LIGHT
        fg = _TEXT_DARK if dark else _TEXT_LIGHT
        subtle = _SUBTLE_DARK if dark else _SUBTLE_LIGHT
        border = _BORDER_DARK if dark else _BORDER_LIGHT

        radius = "12px"
        self._card.setStyleSheet(f"""
            QFrame#qrShareCard {{
                background: {bg};
                border: 1px solid {border};
                border-radius: {radius};
            }}
        """)

        # Hero section: very subtle inner card for the QR.
        for child in self._card.findChildren(QFrame):
            if child.objectName() == "qrShareHero":
                child.setStyleSheet(f"""
                    QFrame#qrShareHero {{
                        background: transparent;
                    }}
                    QLabel#qrShareImage {{
                        background: white;
                        border: 1px solid {border};
                        border-radius: 10px;
                        padding: 14px;
                    }}
                    QLabel#qrShareHint {{ color: {subtle}; font-size: 11px; }}
                """)
            elif child.objectName() == "qrShareTextSection":
                child.setStyleSheet(f"""
                    QFrame#qrShareTextSection {{
                        background: transparent;
                        border-top: 1px solid {border};
                    }}
                    QLabel#qrShareMeta {{ color: {subtle}; font-size: 10px; }}
                    QLabel#qrShareWarn {{
                        color: #f9e2af;
                        background: rgba(249, 226, 175, 0.08);
                        border: 1px solid rgba(249, 226, 175, 0.3);
                        border-radius: 6px;
                        padding: 6px 8px;
                        font-size: 11px;
                    }}
                    QTextEdit#qrShareText {{
                        background: { ('#11111b' if dark else '#f5f5f5') };
                        color: {fg};
                        border: 1px solid {border};
                        border-radius: 8px;
                        padding: 8px;
                        selection-background-color: {accent};
                        selection-color: white;
                    }}
                """)
            elif child.objectName() == "qrShareActions":
                child.setStyleSheet(f"""
                    QFrame#qrShareActions {{
                        background: transparent;
                        border-top: 1px solid {border};
                    }}
                    QPushButton#qrSharePrimaryBtn {{
                        background: {accent};
                        color: white;
                        border: none;
                        border-radius: 6px;
                        padding: 8px 14px;
                        font-weight: bold;
                    }}
                    QPushButton#qrSharePrimaryBtn:hover {{
                        opacity: 0.85;
                    }}
                    QPushButton#qrShareSecondaryBtn {{
                        background: transparent;
                        color: {fg};
                        border: 1px solid {border};
                        border-radius: 6px;
                        padding: 8px 14px;
                    }}
                    QPushButton#qrShareSecondaryBtn:hover {{
                        background: {border};
                    }}
                """)

    # ------------------------------------------------------------------ actions
    def _copy_text(self):
        try:
            QGuiApplication.clipboard().setText(self._text)
        except Exception:
            pass

    def _copy_image(self):
        try:
            img = QImage.fromData(self._png_bytes)
            QGuiApplication.clipboard().setImage(img)
        except Exception:
            pass

    def _save_png(self):
        """Save the QR PNG to a user-chosen file."""
        try:
            from PyQt6.QtWidgets import QFileDialog
            suggested = "qr_share.png"
            path, _ = QFileDialog.getSaveFileName(
                self, "Save QR", suggested, "PNG image (*.png)")
            if path:
                if not path.lower().endswith(".png"):
                    path += ".png"
                img = QImage.fromData(self._png_bytes)
                img.save(path, "PNG")
        except Exception:
            pass
