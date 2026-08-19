"""
qr_share_dialog.py — Floating QR-share popup.

When the user right-clicks on selected text and chooses "Share as QR…",
a compact, draggable, theme-aware popup opens showing the QR code, the
encoded text, and quick-action buttons. The QR is *not* stamped onto the
PDF — this matches MS Edge's "Share as QR" UX where the code is a
transient overlay rather than a permanent page edit.

Responsiveness
--------------
The dialog is fully resizeable via the platform's native resize grip.
On every ``resizeEvent`` we re-render the QR PNG at the label's current
size so the QR always fills its section crisply and the quiet zone
stays proportional — Qt never auto-scales the pixmap, which would clip
the white border that phone cameras need to detect orientation.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
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
    """Compact, draggable, non-modal QR popup.

    The QR scales to fill the hero section on every ``resizeEvent``;
    resizing the dialog via the platform's resize grip is the only
    zoom mechanism (per user request — no zoom buttons).
    """

    # Floor — never render the QR smaller than this. Phone scanners
    # start struggling below ~250 px; we stay safely above that.
    QR_MIN_PX = 280
    # Ceiling — we never up-sample beyond the source PNG's resolution.
    QR_MAX_PX = 900

    # ------------------------------------------------------------------
    def __init__(self, png_bytes: bytes, text: str, parent=None,
                 truncated: bool = False, original_length: int = 0,
                 encoded_length: int = 0):
        super().__init__(parent)
        self._png_bytes = png_bytes
        self._text = text
        self._truncated = truncated
        self._original_length = original_length or len(text)
        self._encoded_length = encoded_length or len(text)
        # Lazy caches for the responsive resize path. ``_qr_source_img``
        # avoids re-decoding PNG bytes on every tick; ``_qr_last_pix``
        # avoids re-scaling the QImage when the holder's target size
        # hasn't actually changed (e.g. mid-drag oscillation).
        self._qr_source_img: QImage | None = None
        self._qr_last_pix: QPixmap | None = None
        self.setWindowTitle("Share as QR")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        # Floor: hero (QR_MIN_PX + chrome) × (text section + actions).
        # The QR can shrink toward QR_MIN_PX; the text + actions rows
        # are independent and keep the dialog usable on tiny screens.
        self.setMinimumSize(
            self.QR_MIN_PX + 80,
            self.QR_MIN_PX + 240,
        )
        self.resize(560, 640)
        self._build_ui()
        self._apply_theme()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Drop shadow for the floating feel under the native frame.
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

        # ---- Hero section: holder + QR (responsive) ----
        hero = QFrame()
        hero.setObjectName("qrShareHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(20, 20, 20, 12)
        hero_layout.setSpacing(6)

        # Holder expands with the dialog — the QR label sits inside
        # and gets a fresh pixmap on every resizeEvent so the QR
        # always fills the available space without Qt auto-scaling.
        self._qr_holder = QFrame()
        self._qr_holder.setObjectName("qrShareHolder")
        holder_layout = QVBoxLayout(self._qr_holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(0)

        self._qr_label = QLabel()
        self._qr_label.setObjectName("qrShareImage")
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setMinimumSize(self.QR_MIN_PX, self.QR_MIN_PX)
        self._qr_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        # ScaledContents off: when Qt paints the label it uses the
        # pixmap's native size, so the quiet zone is preserved pixel
        # for pixel. The pixmap itself is regenerated at the label's
        # current size in resizeEvent().
        self._qr_label.setScaledContents(False)
        holder_layout.addWidget(
            self._qr_label, stretch=1,
            alignment=Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self._qr_holder, stretch=1)

        hint = QLabel("Scan with your phone camera")
        hint.setObjectName("qrShareHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

    # ------------------------------------------------------------------ resize
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_qr_to_fit()

    def showEvent(self, event):
        super().showEvent(event)
        self._render_qr_to_fit()

    def _render_qr_to_fit(self):
        """Re-scale the cached PNG to the holder's current inner size.

        The cache (last QPixmap) keeps resize ticks cheap when the
        target hasn't actually changed — important because
        ``resizeEvent`` fires dozens of times per second during a live
        native-window drag.
        """
        # 6-px safety pad on each side so the QR doesn't kiss the
        # holder edge.
        target = max(self.QR_MIN_PX,
                     min(self._qr_holder.width() - 12,
                         self._qr_holder.height() - 12,
                         self.QR_MAX_PX))
        if target <= 0:
            return

        if self._qr_source_img is None:
            img = QImage.fromData(self._png_bytes)
            if img.isNull():
                self._qr_label.setText("(failed to render QR)")
                return
            self._qr_source_img = img

        if (self._qr_last_pix is not None
                and self._qr_last_pix.width() == target):
            pix = self._qr_last_pix
        else:
            scaled = self._qr_source_img.scaled(
                target, target,
                Qt.AspectRatioMode.IgnoreAspectRatio,  # QRs are square
                Qt.TransformationMode.SmoothTransformation)
            pix = QPixmap.fromImage(scaled)
            self._qr_last_pix = pix
        # Only re-paint / re-size the label when the pixmap actually
        # changed — otherwise ``setFixedSize`` queues a layout pass on
        # every resize tick for no visible benefit.
        if self._qr_label.pixmap() is not pix:
            self._qr_label.setPixmap(pix)
            self._qr_label.setFixedSize(pix.size())

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

        for child in self._card.findChildren(QFrame):
            if child.objectName() == "qrShareHero":
                child.setStyleSheet(f"""
                    QFrame#qrShareHero {{
                        background: transparent;
                    }}
                    QFrame#qrShareHolder {{
                        background: transparent;
                    }}
                    QLabel#qrShareImage {{
                        background: white;
                        border: 1px solid {border};
                        border-radius: 10px;
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
