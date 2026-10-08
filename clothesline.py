#!/usr/bin/env python3
"""Screenshot Clothesline (Tendedero)

Screenshots hung out to dry:
- True 100% per-pixel RGBA transparency on Wayland & X11 (NO black box / void!).
- Single natural clothesline rope with realistic wooden clothespins clamping the cards.
- Delicate light cord in collapsed idle state at the top edge of the screen.
- Smooth pull-down animation: cord lowers down gracefully when expanded.
- Movable & draggable: click and drag the cord or header to position anywhere along the top edge.
- 100% Flicker-free: zero polling of focused windows (VS Code will never flicker).
- Clean emoji buttons with tooltips.
- Click to copy image to clipboard with green feedback badge.
- Double-click (or ✏️) to open Markup Editor; edits save directly in-place to the screenshot file!
"""
import argparse
import math
import os
import platform
import subprocess
import sys
import tempfile
import time

# Clean Snap / VS Code environment variables before any GUI imports to prevent GLib/libc symbol conflicts
for _var in [
    'GTK_PATH',
    'GTK_EXE_PREFIX',
    'GDK_PIXBUF_MODULE_FILE',
    'GDK_PIXBUF_MODULEDIR',
    'GIO_MODULE_DIR',
    'GTK_IM_MODULE_FILE',
    'GSETTINGS_SCHEMA_DIR',
]:
    os.environ.pop(_var, None)
if 'XDG_DATA_DIRS_VSCODE_SNAP_ORIG' in os.environ:
    os.environ['XDG_DATA_DIRS'] = os.environ['XDG_DATA_DIRS_VSCODE_SNAP_ORIG']
if 'XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG' in os.environ:
    os.environ['XDG_CONFIG_DIRS'] = os.environ['XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG']

from PIL import Image

from shots import (
    FolderWatcher,
    copy_image_to_clipboard,
    default_folder,
    fingerprint,
    load_image,
    relative_time_str,
    reveal_in_folder,
)

# Attempt to load GTK3 + Cairo (native per-pixel RGBA transparency on Linux)
HAS_GTK = False
try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Pango', '1.0')
    gi.require_version('PangoCairo', '1.0')
    from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo
    import cairo
    HAS_GTK = True
except Exception:
    HAS_GTK = False

IS_WINDOWS = (platform.system() == 'Windows')

# Dimensions & Tuning
EXPANDED_HEIGHT = 185
COLLAPSED_HEIGHT = 22
COLLAPSED_WIDTH = 250

EXPANDED_CORD_Y = 32
COLLAPSED_CORD_Y = 10

CARD_W = 148
CARD_H = 122
CARD_GAP = 14
THUMB_SLOT = (136, 82)
MAX_HISTORY = 40
VISIBLE_CARDS = 4

TILTS = [-1.1, 0.8, -0.6, 1.0, -0.8, 0.7, -0.9, 0.8]


class Shot:
    """A screenshot: either a file on disk or an image from the clipboard."""

    def __init__(self, path=None, image=None, mtime=None, tilt=0.0):
        self.path = path
        self.image = image
        self.mtime = mtime or (os.path.getmtime(path) if path and os.path.exists(path) else time.time())
        self.tilt = tilt
        self.thumb = None
        self.pixbuf = None
        self.photo = None  # For Tkinter fallback
        self.fp = None
        self.resolution = ''
        self.is_clipboard = (path is None)

    def ensure_thumb(self):
        """Prepare thumbnail image and cached surface."""
        if self.thumb is not None:
            return True
        img = self.image if self.image is not None else load_image(self.path)
        if img is None:
            return False
        self.fp = fingerprint(img)
        w, h = img.size
        self.resolution = f'{w}x{h}'

        thumb_copy = img.copy()
        thumb_copy.thumbnail(THUMB_SLOT, Image.Resampling.LANCZOS)
        self.thumb = thumb_copy.convert('RGBA')

        if HAS_GTK:
            raw = self.thumb.tobytes()
            tw, th = self.thumb.size
            self.pixbuf = GdkPixbuf.Pixbuf.new_from_data(
                raw, GdkPixbuf.Colorspace.RGB, True, 8, tw, th, tw * 4
            )
        return True

    def full(self):
        return self.image if self.image is not None else load_image(self.path)

    def refresh(self):
        self.thumb = None
        self.pixbuf = None
        self.photo = None
        self.image = None
        if self.path and os.path.exists(self.path):
            self.mtime = os.path.getmtime(self.path)
        return self.ensure_thumb()


# ==============================================================================
# GTK3 + Cairo Engine (True Per-Pixel RGBA Transparency on Linux)
# ==============================================================================
if HAS_GTK:
    class QuickLookWindow(Gtk.Window):
        """Full-size screenshot preview window (Quick Look)."""

        def __init__(self, shot, on_edit=None):
            super().__init__(type=Gtk.WindowType.TOPLEVEL)
            self.shot = shot
            self.on_edit = on_edit

            title = os.path.basename(shot.path) if shot.path else 'Screenshot Preview'
            self.set_title(title)
            self.set_position(Gtk.WindowPosition.CENTER)
            self.set_keep_above(True)

            # Dark theme background
            self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.08, 0.10, 0.14, 1.0))

            header = Gtk.HeaderBar(show_close_button=True)
            header.set_title(title)
            header.set_subtitle(f'{shot.resolution} • {relative_time_str(shot.mtime)}')
            self.set_titlebar(header)

            btn_edit = Gtk.Button(label='✏️ Edit')
            btn_edit.set_tooltip_text('Open in Markup & Crop editor (e)')
            btn_edit.connect('clicked', self.on_click_edit)
            header.pack_start(btn_edit)

            btn_copy = Gtk.Button(label='📋 Copy')
            btn_copy.set_tooltip_text('Copy image to clipboard (c)')
            btn_copy.connect('clicked', self.on_click_copy)
            header.pack_start(btn_copy)

            if shot.path and os.path.exists(shot.path):
                btn_folder = Gtk.Button(label='📂 Folder')
                btn_folder.set_tooltip_text('Reveal in file manager')
                btn_folder.connect('clicked', lambda w: reveal_in_folder(shot.path))
                header.pack_start(btn_folder)

            img = shot.full()
            if img:
                screen = self.get_screen()
                max_w = int(screen.get_width() * 0.86)
                max_h = int(screen.get_height() * 0.80)
                iw, ih = img.size
                scale = min(1.0, max_w / max(1, iw), max_h / max(1, ih))
                tw, th = max(10, int(iw * scale)), max(10, int(ih * scale))
                resized = img.resize((tw, th), Image.Resampling.LANCZOS) if scale < 1.0 else img

                raw = resized.convert('RGBA').tobytes()
                pb = GdkPixbuf.Pixbuf.new_from_data(
                    raw, GdkPixbuf.Colorspace.RGB, True, 8, tw, th, tw * 4
                )
                gtk_image = Gtk.Image.new_from_pixbuf(pb)

                event_box = Gtk.EventBox()
                event_box.add(gtk_image)
                event_box.connect('button-press-event', lambda w, e: self.destroy())

                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                box.set_margin_start(10)
                box.set_margin_end(10)
                box.set_margin_top(10)
                box.set_margin_bottom(10)
                box.pack_start(event_box, True, True, 0)
                self.add(box)

            self.connect('key-press-event', self.on_key)
            self.show_all()

        def on_key(self, widget, event):
            if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_space, Gdk.KEY_q):
                self.destroy()
                return True
            elif event.keyval in (Gdk.KEY_e, Gdk.KEY_E):
                self.on_click_edit(None)
                return True
            elif event.keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self.on_click_copy(None)
                return True
            return False

        def on_click_edit(self, widget):
            self.destroy()
            if self.on_edit:
                self.on_edit(self.shot)

        def on_click_copy(self, widget):
            img = self.shot.full()
            if img:
                copy_image_to_clipboard(img)
                try:
                    clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                    full_rgba = img.convert('RGBA')
                    fw, fh = full_rgba.size
                    full_pb = GdkPixbuf.Pixbuf.new_from_data(
                        full_rgba.tobytes(), GdkPixbuf.Colorspace.RGB, True, 8, fw, fh, fw * 4
                    )
                    clip.set_image(full_pb)
                    clip.store()
                except Exception:
                    pass

    class ClotheslineGtkApp:
        """Clothesline desktop interface using GTK3 and Cairo with 100% RGBA transparency."""

        def __init__(self, folder, count=VISIBLE_CARDS, pinned=False):
            self.folder = folder
            self.visible_count = count
            self.pinned = pinned
            self.collapsed = False

            self.shots = []
            self.scroll_offset = 0
            self.hover_shot_idx = None
            self.hover_action = None
            self.toast = None
            self.tooltip = None

            # Dragging & Position
            self.win_x = None
            self.press_pos = None
            self.drag_start_x = None
            self.is_dragging = False

            # Animation state
            self.animating = False
            self.anim_progress = 1.0  # 0.0 = fully collapsed, 1.0 = fully expanded
            self.auto_hide_id = None

            # Setup folder watcher
            self.watcher = FolderWatcher(folder)
            self.watcher.prime()

            # Window setup: POPUP window gives override-redirect (no WM borders, no focus stealing)
            self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
            self.win.set_app_paintable(True)
            self.win.set_keep_above(True)

            screen = self.win.get_screen()
            visual = screen.get_rgba_visual()
            if visual:
                self.win.set_visual(visual)

            # DrawingArea receives Cairo paint events and user input
            self.da = Gtk.DrawingArea()
            self.win.add(self.da)

            self.da.set_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
                | Gdk.EventMask.SCROLL_MASK
            )

            self.da.connect('draw', self.on_draw)
            self.da.connect('motion-notify-event', self.on_motion)
            self.da.connect('button-press-event', self.on_press)
            self.da.connect('button-release-event', self.on_release)
            self.da.connect('enter-notify-event', self.on_enter)
            self.da.connect('leave-notify-event', self.on_leave)
            self.da.connect('scroll-event', self.on_scroll)

            # Load initial existing screenshots
            existing = self.watcher.existing()[:MAX_HISTORY]
            for idx, (mtime, path) in enumerate(existing):
                tilt = TILTS[idx % len(TILTS)]
                shot = Shot(path=path, mtime=mtime, tilt=tilt)
                if shot.ensure_thumb():
                    self.shots.append(shot)

            self.win.show_all()

            # Initial geometry setup
            screen_w = screen.get_width()
            dock_w = self.get_current_width()
            dock_h = EXPANDED_HEIGHT
            self.win_x = max(20, min(screen_w - dock_w - 20, (screen_w - dock_w) // 2 + 180))

            gdk_win = self.win.get_window()
            if gdk_win:
                gdk_win.move_resize(self.win_x, 0, dock_w, dock_h)

            # Pure filesystem watcher (zero compositor flicker)
            GLib.timeout_add_seconds(2, self.poll_files)

            # Periodic toast check
            GLib.timeout_add(200, self.check_toast)

            # Initially schedule auto-hide if not pinned
            if not self.pinned:
                self.schedule_auto_hide(4000)

        def get_expanded_width(self):
            num_cards = min(self.visible_count, max(1, len(self.shots)))
            needed = 44 + num_cards * (CARD_W + CARD_GAP) + 24
            return max(340, needed)

        def get_current_width(self):
            if self.collapsed:
                return COLLAPSED_WIDTH
            return self.get_expanded_width()

        def get_current_height(self):
            if self.collapsed:
                return COLLAPSED_HEIGHT
            return int(COLLAPSED_HEIGHT + (EXPANDED_HEIGHT - COLLAPSED_HEIGHT) * self.anim_progress)

        def update_geometry(self):
            gdk_win = self.win.get_window()
            if not gdk_win:
                return
            screen_w = self.win.get_screen().get_width()
            cur_w = self.get_current_width()
            cur_h = self.get_current_height()

            if self.win_x is None:
                self.win_x = (screen_w - cur_w) // 2
            self.win_x = max(0, min(screen_w - cur_w, self.win_x))
            gdk_win.move_resize(self.win_x, 0, cur_w, cur_h)
            self.da.queue_draw()

        # --- Smooth Animations ---
        def expand(self):
            if not self.collapsed and not self.animating:
                return
            self.cancel_auto_hide()
            self.collapsed = False
            self.animate_transition(opening=True)

        def collapse(self):
            if self.collapsed or self.animating:
                return
            self.cancel_auto_hide()
            self.animate_transition(opening=False)

        def animate_transition(self, opening=True, step=0):
            self.animating = True
            total_steps = 8

            if opening:
                progress = (step + 1) / total_steps
                eased = 1.0 - (1.0 - progress) ** 3
                self.anim_progress = eased
                self.update_geometry()

                if step < total_steps - 1:
                    GLib.timeout_add(16, lambda: self.animate_transition(opening=True, step=step + 1) or False)
                else:
                    self.anim_progress = 1.0
                    self.animating = False
                    self.collapsed = False
                    self.update_geometry()
            else:
                progress = (step + 1) / total_steps
                eased = 1.0 - (progress ** 2)
                self.anim_progress = eased
                self.update_geometry()

                if step < total_steps - 1:
                    GLib.timeout_add(16, lambda: self.animate_transition(opening=False, step=step + 1) or False)
                else:
                    self.anim_progress = 0.0
                    self.animating = False
                    self.collapsed = True
                    self.update_geometry()

        # --- Native Hover Events ---
        def on_enter(self, widget, event):
            self.cancel_auto_hide()
            if self.collapsed:
                self.expand()

        def on_leave(self, widget, event):
            self.hover_shot_idx = None
            self.hover_action = None
            self.tooltip = None
            self.da.queue_draw()
            if not self.collapsed and not self.pinned:
                self.schedule_auto_hide(2000)

        def schedule_auto_hide(self, ms=2000):
            self.cancel_auto_hide()
            if not self.pinned:
                self.auto_hide_id = GLib.timeout_add(ms, self.on_auto_hide_timeout)

        def on_auto_hide_timeout(self):
            self.auto_hide_id = None
            self.collapse()
            return False

        def cancel_auto_hide(self):
            if self.auto_hide_id is not None:
                GLib.source_remove(self.auto_hide_id)
                self.auto_hide_id = None

        # --- Cairo Drawing ---
        def on_draw(self, widget, cr):
            # 1. 100% CLEAR to transparent RGBA (zero black box!)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)

            if self.collapsed:
                self.draw_collapsed_cord(cr)
            else:
                self.draw_expanded_dock(cr)
            return False

        def draw_collapsed_cord(self, cr):
            """Minimal, elegant cord at the top edge."""
            w = COLLAPSED_WIDTH
            cord_y = COLLAPSED_CORD_Y

            # Cord Drop Shadow
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.3)
            cr.set_line_width(3.0)
            cr.move_to(8, cord_y + 1.2)
            cr.curve_to(w * 0.35, cord_y + 2.8, w * 0.65, cord_y + 2.8, w - 8, cord_y + 1.2)
            cr.stroke()

            # Main Hemp Rope
            cr.set_source_rgba(0.84, 0.70, 0.51, 1.0)
            cr.set_line_width(2.6)
            cr.move_to(8, cord_y)
            cr.curve_to(w * 0.35, cord_y + 1.8, w * 0.65, cord_y + 1.8, w - 8, cord_y)
            cr.stroke()

            # Rope Highlight Strand
            cr.set_source_rgba(0.98, 0.93, 0.85, 0.75)
            cr.set_line_width(0.9)
            cr.move_to(8, cord_y - 0.7)
            cr.curve_to(w * 0.35, cord_y + 1.1, w * 0.65, cord_y + 1.1, w - 8, cord_y - 0.7)
            cr.stroke()

            # End Mount Eyelets
            for ax in [8, w - 8]:
                cr.set_source_rgba(0.48, 0.55, 0.65, 1.0)
                cr.arc(ax, cord_y, 3.2, 0, 2 * math.pi)
                cr.fill()

            # Center Wooden Grip Pin
            mid_x = w / 2.0
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.35)
            self.rounded_rect(cr, mid_x - 3, cord_y - 3, 7, 14, 2)
            cr.fill()

            cr.set_source_rgba(0.87, 0.69, 0.45, 1.0)
            self.rounded_rect(cr, mid_x - 4, cord_y - 4, 7, 14, 2)
            cr.fill_preserve()
            cr.set_source_rgba(0.58, 0.40, 0.22, 1.0)
            cr.set_line_width(0.8)
            cr.stroke()

            cr.set_source_rgba(0.35, 0.38, 0.45, 1.0)
            cr.rectangle(mid_x - 4, cord_y + 2, 7, 2)
            cr.fill()

        def draw_expanded_dock(self, cr):
            """Hanging clothesline with cards, pins, and controls."""
            w = self.get_expanded_width()
            progress = self.anim_progress

            cord_y = int(COLLAPSED_CORD_Y + (EXPANDED_CORD_Y - COLLAPSED_CORD_Y) * progress)

            # 1. Header controls (Pill buttons at top right)
            btn_x = w - 100
            btn_y = max(4, cord_y - 26)
            btns = [
                ('btn_pin', '📌', 'Pinned (keep open)' if self.pinned else 'Auto-hide'),
                ('btn_folder', '📂', 'Open screenshots folder'),
                ('btn_clear', '🗑️', 'Clear all from line'),
            ]
            for i, (action_id, emoji, _) in enumerate(btns):
                bx = btn_x + i * 30
                is_active = (action_id == 'btn_pin' and self.pinned)
                is_hov = (self.hover_action == action_id)

                if is_active:
                    cr.set_source_rgba(0.15, 0.35, 0.70, 0.9)
                elif is_hov:
                    cr.set_source_rgba(0.22, 0.28, 0.38, 0.95)
                else:
                    cr.set_source_rgba(0.12, 0.16, 0.22, 0.85)

                self.rounded_rect(cr, bx, btn_y, 26, 22, 11)
                cr.fill_preserve()
                cr.set_source_rgba(0.35, 0.45, 0.60, 0.9 if is_active or is_hov else 0.6)
                cr.set_line_width(1.0)
                cr.stroke()

                layout = PangoCairo.create_layout(cr)
                layout.set_text(emoji, -1)
                desc = Pango.FontDescription('Sans 10')
                layout.set_font_description(desc)
                cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                cr.move_to(bx + 5, btn_y + 2)
                PangoCairo.show_layout(cr, layout)

            # 2. Main Clothesline Cord
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.28)
            cr.set_line_width(3.5)
            cr.move_to(16, cord_y + 1.5)
            cr.curve_to(w * 0.35, cord_y + 4.2, w * 0.65, cord_y + 4.2, w - 16, cord_y + 1.5)
            cr.stroke()

            cr.set_source_rgba(0.84, 0.70, 0.51, 1.0)
            cr.set_line_width(3.0)
            cr.move_to(16, cord_y)
            cr.curve_to(w * 0.35, cord_y + 2.7, w * 0.65, cord_y + 2.7, w - 16, cord_y)
            cr.stroke()

            cr.set_source_rgba(0.98, 0.93, 0.85, 0.75)
            cr.set_line_width(1.0)
            cr.move_to(16, cord_y - 0.8)
            cr.curve_to(w * 0.35, cord_y + 1.8, w * 0.65, cord_y + 1.8, w - 16, cord_y - 0.8)
            cr.stroke()

            # End anchors
            for ax in [16, w - 16]:
                cr.set_source_rgba(0.48, 0.55, 0.65, 1.0)
                cr.arc(ax, cord_y, 3.8, 0, 2 * math.pi)
                cr.fill()

            # 3. Hanging Cards
            visible_shots = self.shots[self.scroll_offset : self.scroll_offset + self.visible_count]
            start_x = 24
            card_offset_y = int((1.0 - progress) * 45)

            if not visible_shots:
                # Empty message
                layout = PangoCairo.create_layout(cr)
                layout.set_text('✨ Take a screenshot to hang it here', -1)
                desc = Pango.FontDescription('Sans 10')
                layout.set_font_description(desc)
                cr.set_source_rgba(0.7, 0.78, 0.88, 0.9)
                cr.move_to(start_x + 30, cord_y + 40)
                PangoCairo.show_layout(cr, layout)
                return

            for i, shot in enumerate(visible_shots):
                idx = self.scroll_offset + i
                cx = start_x + i * (CARD_W + CARD_GAP)
                cy = cord_y + 12 - card_offset_y
                is_hovered = (self.hover_shot_idx == idx)
                is_pin_hov = (self.hover_action == f'unpin_{idx}')

                cr.save()
                mid_cx = cx + CARD_W / 2.0
                cr.translate(mid_cx, cy)
                cr.rotate(math.radians(shot.tilt))
                cr.translate(-mid_cx, -cy)

                # Card drop shadow
                cr.set_source_rgba(0.0, 0.0, 0.0, 0.35)
                self.rounded_rect(cr, cx, cy + 4, CARD_W, CARD_H, 6)
                cr.fill()

                # Card container
                cr.set_source_rgba(0.12, 0.15, 0.21, 0.96)
                self.rounded_rect(cr, cx, cy, CARD_W, CARD_H, 6)
                cr.fill_preserve()

                if is_hovered:
                    cr.set_source_rgba(0.38, 0.65, 0.98, 1.0)
                    cr.set_line_width(1.8)
                else:
                    cr.set_source_rgba(0.24, 0.31, 0.42, 0.9)
                    cr.set_line_width(1.0)
                cr.stroke()

                # Thumbnail slot
                slot_w = CARD_W - 12
                slot_h = 80
                slot_x = cx + 6
                slot_y = cy + 6

                cr.set_source_rgba(0.07, 0.09, 0.13, 1.0)
                self.rounded_rect(cr, slot_x, slot_y, slot_w, slot_h, 4)
                cr.fill()

                # Render thumbnail pixbuf centered
                if shot.ensure_thumb() and shot.pixbuf:
                    pw = shot.pixbuf.get_width()
                    ph = shot.pixbuf.get_height()
                    tx = slot_x + (slot_w - pw) // 2
                    ty = slot_y + (slot_h - ph) // 2
                    Gdk.cairo_set_source_pixbuf(cr, shot.pixbuf, tx, ty)
                    cr.paint()

                # Bottom info row:
                # Left: Time
                layout = PangoCairo.create_layout(cr)
                desc = Pango.FontDescription('Sans 8')
                layout.set_font_description(desc)
                time_str = relative_time_str(shot.mtime)
                layout.set_text(time_str, -1)
                cr.set_source_rgba(0.65, 0.72, 0.82, 1.0)
                cr.move_to(cx + 8, cy + 98)
                PangoCairo.show_layout(cr, layout)

                # Right: Action Buttons (🔍 View and ✏️ Edit)
                by_btn = cy + 92

                # 1. 🔍 View Button
                bx_view = cx + CARD_W - 56
                hov_view = (self.hover_action == f'view_{idx}')
                cr.set_source_rgba(0.20, 0.28, 0.40, 0.95 if hov_view else 0.8)
                self.rounded_rect(cr, bx_view, by_btn, 24, 22, 5)
                cr.fill_preserve()
                cr.set_source_rgba(0.38, 0.65, 0.98 if hov_view else 0.45, 0.9 if hov_view else 0.6)
                cr.set_line_width(1.0)
                cr.stroke()

                layout.set_text('🔍', -1)
                desc_icon = Pango.FontDescription('Sans 9')
                layout.set_font_description(desc_icon)
                cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                cr.move_to(bx_view + 4, by_btn + 3)
                PangoCairo.show_layout(cr, layout)

                # 2. ✏️ Edit Button
                bx_edit = cx + CARD_W - 28
                hov_edit = (self.hover_action == f'edit_{idx}')
                cr.set_source_rgba(0.20, 0.28, 0.40, 0.95 if hov_edit else 0.8)
                self.rounded_rect(cr, bx_edit, by_btn, 24, 22, 5)
                cr.fill_preserve()
                cr.set_source_rgba(0.38, 0.65, 0.98 if hov_edit else 0.45, 0.9 if hov_edit else 0.6)
                cr.set_line_width(1.0)
                cr.stroke()

                layout.set_text('✏️', -1)
                cr.move_to(bx_edit + 4, by_btn + 3)
                PangoCairo.show_layout(cr, layout)

                # Toast badge (e.g. "✓ Copied!")
                if self.toast and self.toast.get('shot_idx') == idx:
                    cr.set_source_rgba(0.12, 0.68, 0.38, 0.95)
                    self.rounded_rect(cr, cx + 24, cy + 36, CARD_W - 48, 26, 13)
                    cr.fill_preserve()
                    cr.set_source_rgba(1.0, 1.0, 1.0, 0.8)
                    cr.set_line_width(1.0)
                    cr.stroke()

                    layout.set_text(self.toast.get('text', ''), -1)
                    desc_toast = Pango.FontDescription('Sans Bold 9')
                    layout.set_font_description(desc_toast)
                    ink, logical = layout.get_pixel_extents()
                    cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                    cr.move_to(cx + 24 + (CARD_W - 48 - logical.width) // 2, cy + 40)
                    PangoCairo.show_layout(cr, layout)

                # ONE Single Wooden Clothespin in the Center!
                pin_x = cx + (CARD_W - 10) / 2
                self.draw_pin(cr, pin_x, cord_y + 2 - card_offset_y, is_hover=is_pin_hov)

                cr.restore()

            # 4. Scroll navigation arrows (if more cards exist than fit)
            if self.scroll_offset > 0:
                self.draw_nav_arrow(cr, 4, cord_y + 40, '◀', self.hover_action == 'nav_left')
            if self.scroll_offset + self.visible_count < len(self.shots):
                self.draw_nav_arrow(cr, w - 24, cord_y + 40, '▶', self.hover_action == 'nav_right')

        def draw_pin(self, cr, px, py, is_hover=False):
            """Single realistic wooden clothespin clamping the line and card."""
            # Shadow
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.35)
            self.rounded_rect(cr, px + 1, py - 9, 10, 26, 2)
            cr.fill()

            # Wooden body
            if is_hover:
                cr.set_source_rgba(0.96, 0.78, 0.52, 1.0)  # Brighter on hover
            else:
                cr.set_source_rgba(0.87, 0.69, 0.45, 1.0)
            self.rounded_rect(cr, px, py - 10, 10, 26, 2)
            cr.fill_preserve()
            cr.set_source_rgba(0.68 if is_hover else 0.58, 0.48 if is_hover else 0.40, 0.25 if is_hover else 0.22, 1.0)
            cr.set_line_width(1.0 if is_hover else 0.8)
            cr.stroke()

            # Metal coil spring in groove
            cr.set_source_rgba(0.35, 0.38, 0.45, 1.0)
            cr.rectangle(px, py - 1, 10, 3)
            cr.fill()
            cr.set_source_rgba(0.85, 0.90, 0.95, 0.9)
            cr.rectangle(px + 2, py - 1, 6, 1)
            cr.fill()

        def draw_nav_arrow(self, cr, ax, ay, symbol, is_hover):
            cr.set_source_rgba(0.20, 0.26, 0.36, 0.95 if is_hover else 0.8)
            self.rounded_rect(cr, ax, ay, 20, 36, 6)
            cr.fill_preserve()
            cr.set_source_rgba(0.38, 0.65, 0.98 if is_hover else 0.55, 0.8)
            cr.set_line_width(1.0)
            cr.stroke()

            layout = PangoCairo.create_layout(cr)
            layout.set_text(symbol, -1)
            desc = Pango.FontDescription('Sans 10')
            layout.set_font_description(desc)
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            cr.move_to(ax + 4, ay + 10)
            PangoCairo.show_layout(cr, layout)

        def rounded_rect(self, cr, x, y, w, h, r):
            cr.new_sub_path()
            cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
            cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
            cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
            cr.close_path()

        # --- Motion & Hit Testing ---
        def on_motion(self, widget, event):
            self.cancel_auto_hide()
            ex, ey = event.x, event.y

            # Handle window dragging horizontally or card pull-down
            if self.press_pos is not None and (event.state & Gdk.ModifierType.BUTTON1_MASK):
                dx = event.x_root - self.press_pos[0]
                dy = event.y_root - self.press_pos[1]

                # Pull down gesture to remove/drop card from clothesline
                if not self.collapsed and self.hover_shot_idx is not None and dy > 45:
                    self.remove_shot(self.hover_shot_idx)
                    self.press_pos = None
                    self.is_dragging = False
                    return

                can_drag = self.collapsed or (self.hover_shot_idx is None and self.hover_action is None)
                if can_drag and (abs(dx) > 4 or abs(dy) > 4):
                    self.is_dragging = True
                    screen_w = self.win.get_screen().get_width()
                    cur_w = self.get_current_width()
                    self.win_x = max(0, min(screen_w - cur_w, int(event.x_root - self.drag_start_x)))
                    gdk_win = self.win.get_window()
                    if gdk_win:
                        gdk_win.move(self.win_x, 0)
                    return


            prev_shot = self.hover_shot_idx
            prev_act = self.hover_action

            self.hover_shot_idx = None
            self.hover_action = None

            if not self.collapsed:
                w = self.get_expanded_width()
                cord_y = EXPANDED_CORD_Y

                # Check header buttons
                btn_x = w - 100
                btn_y = max(4, cord_y - 26)
                for i, act_id in enumerate(['btn_pin', 'btn_folder', 'btn_clear']):
                    bx = btn_x + i * 30
                    if bx <= ex <= bx + 26 and btn_y <= ey <= btn_y + 22:
                        self.hover_action = act_id
                        break

                # Check nav buttons
                if self.hover_action is None:
                    if self.scroll_offset > 0 and 4 <= ex <= 24 and cord_y + 40 <= ey <= cord_y + 76:
                        self.hover_action = 'nav_left'
                    elif self.scroll_offset + self.visible_count < len(self.shots) and w - 24 <= ex <= w - 4 and cord_y + 40 <= ey <= cord_y + 76:
                        self.hover_action = 'nav_right'

                # Check cards and single clothespin
                if self.hover_action is None:
                    visible_shots = self.shots[self.scroll_offset : self.scroll_offset + self.visible_count]
                    start_x = 24
                    for i, shot in enumerate(visible_shots):
                        idx = self.scroll_offset + i
                        cx = start_x + i * (CARD_W + CARD_GAP)
                        cy = cord_y + 12

                        # 1. Top single wooden clothespin (Click to unpin/drop!)
                        pin_cx = cx + CARD_W / 2.0
                        if pin_cx - 8 <= ex <= pin_cx + 8 and cord_y - 12 <= ey <= cy + 4:
                            self.hover_action = f'unpin_{idx}'
                            self.hover_shot_idx = idx
                            break

                        # 2. Card body & bottom buttons
                        if cx <= ex <= cx + CARD_W and cy <= ey <= cy + CARD_H:
                            self.hover_shot_idx = idx

                            # Bottom buttons
                            bx_view = cx + CARD_W - 56
                            bx_edit = cx + CARD_W - 28
                            by_btn = cy + 92

                            if bx_view <= ex <= bx_view + 24 and by_btn <= ey <= by_btn + 24:
                                self.hover_action = f'view_{idx}'
                            elif bx_edit <= ex <= bx_edit + 24 and by_btn <= ey <= by_btn + 24:
                                self.hover_action = f'edit_{idx}'
                            break

            # Update desktop tooltips
            if self.hover_action and self.hover_action.startswith('unpin_'):
                self.da.set_tooltip_text('Unclip & drop from clothesline')
            elif self.hover_action and self.hover_action.startswith('view_'):
                self.da.set_tooltip_text('View larger image (Quick Look)')
            elif self.hover_action and self.hover_action.startswith('edit_'):
                self.da.set_tooltip_text('Markup & crop')
            elif self.hover_action == 'btn_pin':
                self.da.set_tooltip_text('Keep open' if not self.pinned else 'Auto-hide')
            elif self.hover_action == 'btn_folder':
                self.da.set_tooltip_text('Open screenshots folder')
            elif self.hover_action == 'btn_clear':
                self.da.set_tooltip_text('Clear all from clothesline')
            elif self.hover_shot_idx is not None:
                self.da.set_tooltip_text('Click to copy • Double-click to edit')
            else:
                self.da.set_tooltip_text(None)

            if prev_shot != self.hover_shot_idx or prev_act != self.hover_action:
                self.da.queue_draw()

        def on_press(self, widget, event):
            if event.button == 1:
                self.press_pos = (event.x_root, event.y_root)
                self.drag_start_x = event.x_root - (self.win_x or 0)
                self.is_dragging = False

                if event.type == Gdk.EventType._2BUTTON_PRESS:
                    # Double-click opens editor
                    if self.hover_shot_idx is not None and self.hover_shot_idx < len(self.shots):
                        self.open_editor(self.shots[self.hover_shot_idx])
            elif event.button == 3:
                # Right-click context menu
                self.show_context_menu(event)

        def on_release(self, widget, event):
            if event.button == 1:
                was_dragging = self.is_dragging
                self.press_pos = None
                self.is_dragging = False

                if was_dragging:
                    return

                if self.collapsed:
                    self.expand()
                    return

                # Handle clicks
                if self.hover_action == 'btn_pin':
                    self.pinned = not self.pinned
                    self.cancel_auto_hide()
                    self.da.queue_draw()
                elif self.hover_action == 'btn_folder':
                    reveal_in_folder(self.folder)
                elif self.hover_action == 'btn_clear':
                    self.clear_all()
                elif self.hover_action == 'nav_left':
                    self.scroll_left()
                elif self.hover_action == 'nav_right':
                    self.scroll_right()
                elif self.hover_action and self.hover_action.startswith('unpin_'):
                    idx = int(self.hover_action.split('_')[1])
                    self.remove_shot(idx)
                elif self.hover_action and self.hover_action.startswith('view_'):
                    idx = int(self.hover_action.split('_')[1])
                    self.open_quick_look(self.shots[idx])
                elif self.hover_action and self.hover_action.startswith('edit_'):
                    idx = int(self.hover_action.split('_')[1])
                    self.open_editor(self.shots[idx])
                elif self.hover_shot_idx is not None:
                    # Clicking card body copies to clipboard
                    self.copy_shot(self.hover_shot_idx)

        def on_scroll(self, widget, event):
            if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
                self.scroll_left()
            elif event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
                self.scroll_right()

        def scroll_left(self):
            if self.scroll_offset > 0:
                self.scroll_offset -= 1
                self.da.queue_draw()

        def scroll_right(self):
            if self.scroll_offset + self.visible_count < len(self.shots):
                self.scroll_offset += 1
                self.da.queue_draw()

        # --- Context Menu ---
        def show_context_menu(self, event):
            if self.hover_shot_idx is None or self.hover_shot_idx >= len(self.shots):
                return
            idx = self.hover_shot_idx
            shot = self.shots[idx]

            menu = Gtk.Menu()

            item_copy = Gtk.MenuItem(label='📋 Copy Image to Clipboard')
            item_copy.connect('activate', lambda w: self.copy_shot(idx))
            menu.append(item_copy)

            item_view = Gtk.MenuItem(label='🔍 View Full-Size Image')
            item_view.connect('activate', lambda w: self.open_quick_look(shot))
            menu.append(item_view)

            item_edit = Gtk.MenuItem(label='✏️ Open in Editor (Markup / Crop)')
            item_edit.connect('activate', lambda w: self.open_editor(shot))
            menu.append(item_edit)

            if shot.path and os.path.exists(shot.path):
                item_folder = Gtk.MenuItem(label='📂 Reveal in File Manager')
                item_folder.connect('activate', lambda w: reveal_in_folder(shot.path))
                menu.append(item_folder)

            menu.append(Gtk.SeparatorMenuItem())

            item_remove = Gtk.MenuItem(label='🗑️ Unclip / Drop from Clothesline')
            item_remove.connect('activate', lambda w: self.remove_shot(idx))
            menu.append(item_remove)

            menu.show_all()
            menu.popup(None, None, None, None, event.button, event.time)

        def open_quick_look(self, shot):
            QuickLookWindow(shot, on_edit=self.open_editor)

        # --- Actions ---
        def copy_shot(self, idx):
            if idx >= len(self.shots):
                return
            shot = self.shots[idx]
            img = shot.full()
            if img:
                ok = copy_image_to_clipboard(img)
                try:
                    clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                    full_rgba = img.convert('RGBA')
                    fw, fh = full_rgba.size
                    full_pb = GdkPixbuf.Pixbuf.new_from_data(
                        full_rgba.tobytes(), GdkPixbuf.Colorspace.RGB, True, 8, fw, fh, fw * 4
                    )
                    clip.set_image(full_pb)
                    clip.store()
                    ok = True
                except Exception:
                    pass
                self.show_toast(idx, '✓ Copied!' if ok else 'Copy failed')


        def show_toast(self, idx, text):
            self.toast = {'shot_idx': idx, 'text': text, 'expires': time.time() + 1.8}
            self.da.queue_draw()

        def check_toast(self):
            if self.toast and time.time() > self.toast.get('expires', 0):
                self.toast = None
                self.da.queue_draw()
            return True

        def remove_shot(self, idx):
            if 0 <= idx < len(self.shots):
                del self.shots[idx]
                if self.scroll_offset > 0 and self.scroll_offset >= len(self.shots):
                    self.scroll_offset = max(0, len(self.shots) - 1)
                self.hover_shot_idx = None
                self.hover_action = None
                self.update_geometry()

        def clear_all(self):
            self.shots.clear()
            self.scroll_offset = 0
            self.hover_shot_idx = None
            self.hover_action = None
            self.update_geometry()

        def open_editor(self, shot):
            img = shot.full()
            if img is None:
                return

            # Ensure we have a valid file path for standalone editor invocation
            target_path = shot.path
            if not target_path or not os.path.exists(target_path):
                tmp = tempfile.NamedTemporaryFile(suffix='.png', prefix='clothesline_shot_', delete=False)
                img.save(tmp.name)
                tmp.close()
                target_path = tmp.name
                shot.path = target_path

            editor_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'editor.py')
            subprocess.Popen([sys.executable, editor_script, target_path])

        def add_shot(self, shot):
            if any(s.fp is not None and s.fp == shot.fp for s in self.shots[:20]):
                return
            self.shots.insert(0, shot)
            del self.shots[MAX_HISTORY:]
            self.scroll_offset = 0
            self.expand()
            self.update_geometry()
            self.schedule_auto_hide(6000)

        def poll_files(self):
            """Silently check filesystem for new screenshots or modifications."""
            # Check for new files
            for path in reversed(self.watcher.new_paths()):
                shot = Shot(path=path, tilt=TILTS[len(self.shots) % len(TILTS)])
                if shot.ensure_thumb():
                    self.add_shot(shot)
                else:
                    self.watcher.seen.discard(path)

            # Check if any existing shot was edited on disk
            changed = False
            for shot in self.shots:
                if shot.path and os.path.exists(shot.path):
                    current_mtime = os.path.getmtime(shot.path)
                    if current_mtime > shot.mtime + 0.5:
                        shot.refresh()
                        changed = True

            if changed:
                self.da.queue_draw()

            return True


# ==============================================================================
# Tkinter Engine (Fallback for Windows with -transparentcolor)
# ==============================================================================
else:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from PIL import ImageTk
    from editor import Editor

    class ClotheslineTkApp:
        """Tkinter fallback interface for Windows."""

        def __init__(self, root, folder, count=VISIBLE_CARDS, pinned=False):
            self.root = root
            self.folder = folder
            self.visible_count = count
            self.pinned = pinned
            self.collapsed = False

            self.shots = []
            self.scroll_offset = 0
            self.hover_shot_idx = None
            self.hover_action = None
            self.toast = None

            self.win_x = None
            self.drag_start_x = None
            self.is_dragging = False

            self.animating = False
            self.anim_offset_y = 0
            self.auto_hide_job = None
            self.current_geo = None

            self.watcher = FolderWatcher(folder)
            self.watcher.prime()

            root.title('Clothesline')
            root.overrideredirect(True)
            root.attributes('-topmost', True)

            # Windows transparency colorkey
            root.attributes('-transparentcolor', '#000001')
            root.configure(bg='#000001')
            canvas_bg = '#000001'

            self.canvas = tk.Canvas(root, width=COLLAPSED_WIDTH, height=COLLAPSED_HEIGHT,
                                    bg=canvas_bg, highlightthickness=0, cursor='hand2')
            self.canvas.pack(fill='both', expand=True)

            self.canvas.bind('<Motion>', self.on_motion)
            self.canvas.bind('<ButtonPress-1>', self.on_press)
            self.canvas.bind('<B1-Motion>', self.on_drag)
            self.canvas.bind('<ButtonRelease-1>', self.on_release)
            self.canvas.bind('<Double-Button-1>', self.on_double_click)
            self.canvas.bind('<Enter>', self.on_enter)
            self.canvas.bind('<Leave>', self.on_leave)

            existing = self.watcher.existing()[:MAX_HISTORY]
            for idx, (mtime, path) in enumerate(existing):
                tilt = TILTS[idx % len(TILTS)]
                shot = Shot(path=path, mtime=mtime, tilt=tilt)
                if shot.ensure_thumb():
                    shot.photo = ImageTk.PhotoImage(shot.thumb)
                    self.shots.append(shot)

            self.update_geometry()
            self.draw()
            self.poll_files()

        def update_geometry(self):
            w = COLLAPSED_WIDTH if self.collapsed else 720
            h = COLLAPSED_HEIGHT if self.collapsed else EXPANDED_HEIGHT
            screen_w = self.root.winfo_screenwidth()
            if self.win_x is None:
                self.win_x = (screen_w - w) // 2
            self.win_x = max(0, min(screen_w - w, self.win_x))
            self.root.geometry(f'{w}x{h}+{self.win_x}+0')

        def on_enter(self, e):
            if self.collapsed:
                self.collapsed = False
                self.update_geometry()
                self.draw()

        def on_leave(self, e):
            if not self.collapsed and not self.pinned:
                self.collapsed = True
                self.update_geometry()
                self.draw()

        def on_motion(self, e): pass
        def on_press(self, e): pass
        def on_drag(self, e): pass
        def on_release(self, e): pass
        def on_double_click(self, e): pass
        def draw(self): pass
        def poll_files(self):
            self.root.after(2000, self.poll_files)


def main():
    parser = argparse.ArgumentParser(description='Screenshot Clothesline: Screenshots hung out to dry.')
    parser.add_argument('--folder', default=default_folder(), help='folder your system saves screenshots to')
    parser.add_argument('--count', type=int, default=VISIBLE_CARDS, help='number of screenshots visible on the line (default 4)')
    parser.add_argument('--pinned', action='store_true', help='keep clothesline pinned open (no auto-hide)')
    args = parser.parse_args()

    os.makedirs(args.folder, exist_ok=True)

    if HAS_GTK:
        app = ClotheslineGtkApp(args.folder, count=max(1, args.count), pinned=args.pinned)
        Gtk.main()
    else:
        root = tk.Tk()
        ClotheslineTkApp(root, args.folder, count=max(1, args.count), pinned=args.pinned)
        root.mainloop()


if __name__ == '__main__':
    main()
