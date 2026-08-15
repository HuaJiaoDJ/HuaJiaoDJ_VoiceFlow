"""Floating waveform HUD shown while dictating.

A borderless, click-through, *non-activating* NSPanel: it must never take key
focus, or the synthetic Cmd+V would paste into the overlay's app instead of
whatever the user was typing in. Cocoa requires all of this on the main thread,
so the public functions marshal onto it via performSelectorOnMainThread.
"""
import json
import math
import time

import objc
from AppKit import (
    NSApplication,
    NSEvent,
    NSBezierPath,
    NSColor,
    NSGraphicsContext,
    NSPanel,
    NSRectFillUsingOperation,
    NSScreen,
    NSView,
)
from Foundation import NSMakeRect, NSObject, NSRunLoop, NSTimer

NSRunLoopCommonModes = "kCFRunLoopCommonModes"

NSCompositingOperationCopy = 2

# Constants (spelled out so we don't depend on a particular pyobjc version).
NSWindowStyleMaskBorderless = 0
NSWindowStyleMaskNonactivatingPanel = 1 << 7
NSBackingStoreBuffered = 2
NSStatusWindowLevel = 25
NSApplicationActivationPolicyAccessory = 1
NSWindowCollectionBehaviorCanJoinAllSpaces = 1 << 0
NSWindowCollectionBehaviorStationary = 1 << 4
NSWindowCollectionBehaviorFullScreenAuxiliary = 1 << 8

PANEL_W, PANEL_H = 156.0, 64.0  # compact ellipse
FPS = 30.0

# Gradient stops sampled across the ribbon: cyan -> violet -> pink.
STOPS = ((0.31, 0.85, 1.00), (0.65, 0.55, 0.98), (1.00, 0.44, 0.85))


def _grad(t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    if t < 0.5:
        a, b, u = STOPS[0], STOPS[1], t * 2.0
    else:
        a, b, u = STOPS[1], STOPS[2], (t - 0.5) * 2.0
    return tuple(a[i] + (b[i] - a[i]) * u for i in range(3))


class WaveView(NSView):
    """Draws the reactive ribbon. All state lives in plain Python attrs."""

    def initWithFrame_(self, frame):
        self = objc.super(WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.recorder = None
        self.state = "listening"  # listening | processing
        self.phase = 0.0
        self.amp = 0.0          # smoothed 0..1
        self.alpha = 0.0        # current window opacity
        self.alpha_target = 0.0
        self.t0 = time.time()
        return self

    # ---- animation ----

    def tick_(self, _timer):
        # Ease the window opacity toward its target (fade in/out).
        if abs(self.alpha - self.alpha_target) > 0.01:
            self.alpha += (self.alpha_target - self.alpha) * 0.25
            w = self.window()
            if w is not None:
                w.setAlphaValue_(self.alpha)
        elif self.alpha != self.alpha_target:
            self.alpha = self.alpha_target
            w = self.window()
            if w is not None:
                w.setAlphaValue_(self.alpha)
                if self.alpha == 0.0:
                    w.orderOut_(None)

        if self.state == "processing":
            target = 0.34
            self.phase += 0.34
        else:
            lvl = self.recorder.level if self.recorder is not None else 0.0
            target = min(1.0, lvl * 1.25)
            # Faster attack than release so speech onsets feel immediate.
            self.phase += 0.13 + 0.22 * self.amp
        k = 0.45 if target > self.amp else 0.12
        self.amp += (target - self.amp) * k
        self.setNeedsDisplay_(True)

    # ---- drawing ----

    def drawRect_(self, _rect):
        b = self.bounds()
        w, h = b.size.width, b.size.height
        cx, cy = w / 2.0, h / 2.0

        # Clear to transparent first (Copy, not the default blend) so the
        # area outside the circle stays see-through.
        NSColor.clearColor().set()
        NSRectFillUsingOperation(b, NSCompositingOperationCopy)

        inset = 3.0
        # Semi-axes of the ellipse.
        ax, by = (w - inset * 2.0) / 2.0, (h - inset * 2.0) / 2.0
        disc_rect = NSMakeRect(cx - ax, cy - by, ax * 2.0, by * 2.0)
        disc = NSBezierPath.bezierPathWithOvalInRect_(disc_rect)

        # Translucent dark ellipse.
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.06, 0.11, 0.85).set()
        disc.fill()

        # --- ribbon, clipped inside the ellipse ---
        NSGraphicsContext.saveGraphicsState()
        disc.addClip()

        span = ax * 2.0 * 0.54
        x0 = cx - span / 2.0
        segs = 64
        amp_px = (0.10 + 0.90 * self.amp) * (by * 0.62)
        layers = ((1.00, 1.0, 0.0, 2.0), (0.62, 1.7, 2.0, 1.4), (0.40, 2.6, 4.1, 1.1))
        for li, (scale, freq, off, lw) in enumerate(layers):
            prev = None
            for i in range(segs + 1):
                u = i / float(segs)
                x = x0 + span * u
                # Taper toward both ends so the ribbon floats inside the circle.
                env = math.sin(math.pi * u) ** 1.4
                if self.state == "processing":
                    # A travelling pulse instead of level-reactive motion.
                    c = (self.phase * 0.16) % 1.4 - 0.2
                    env *= math.exp(-((u - c) ** 2) / 0.045) * 1.6 + 0.18
                y = cy + amp_px * scale * env * math.sin(
                    u * math.pi * 2.0 * freq * 1.6 + self.phase + off
                )
                if prev is not None:
                    r, g, bl = _grad(u)
                    a = (0.95 if li == 0 else 0.55) * self.alpha_target
                    seg = NSBezierPath.bezierPath()
                    seg.moveToPoint_(prev)
                    seg.lineToPoint_((x, y))
                    seg.setLineCapStyle_(1)  # round
                    if li == 0:  # glow pass under the main line
                        NSColor.colorWithCalibratedRed_green_blue_alpha_(
                            r, g, bl, a * 0.22
                        ).set()
                        seg.setLineWidth_(lw + 4.0)
                        seg.stroke()
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, bl, a).set()
                    seg.setLineWidth_(lw)
                    seg.stroke()
                prev = (x, y)

        NSGraphicsContext.restoreGraphicsState()


class OverlayController(NSObject):
    """Owns the panel; its show/hide methods are called on the main thread."""

    def initWithRecorder_(self, recorder):
        self = objc.super(OverlayController, self).init()
        if self is None:
            return None
        self.y_offset = 120.0
        rect = NSMakeRect(0, 0, PANEL_W, PANEL_H)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setLevel_(NSStatusWindowLevel)
        # Draggable by default: grab it anywhere to move it. `locked` in the
        # config restores pure click-through for anyone who never wants it to
        # intercept a click. Non-activating, so dragging never steals focus.
        self.panel.setIgnoresMouseEvents_(False)
        self.panel.setMovable_(True)
        self.panel.setMovableByWindowBackground_(True)
        self.panel.setHasShadow_(True)
        self.panel.setAlphaValue_(0.0)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.view = WaveView.alloc().initWithFrame_(rect)
        self.view.recorder = recorder
        self.panel.setContentView_(self.view)
        self.saved_position = None
        self._placing = False   # True while we move the panel ourselves
        self.panel.setDelegate_(self)   # for windowDidMove:
        # Register in *common* modes. A plain scheduled timer only runs in the
        # default mode, so it stops firing while a menu is open or the user is
        # scrolling/dragging — the panel would be ordered front but stuck at
        # alpha 0, i.e. invisible.
        self.timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FPS, self.view, "tick:", None, True
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(self.timer, NSRunLoopCommonModes)
        return self

    def _clamp_onscreen(self, x, y):
        """Keep the pill reachable if a display was unplugged or rearranged."""
        for s in NSScreen.screens():
            f = s.frame()
            if (f.origin.x - PANEL_W < x < f.origin.x + f.size.width
                    and f.origin.y - PANEL_H < y < f.origin.y + f.size.height):
                return x, y
        return None  # saved spot is off every display; fall back to auto

    def _reposition(self):
        """Place the HUD where the user dragged it, else auto-place it.

        NSScreen.mainScreen() is the screen with the *key window* — but this is
        an accessory app that never takes key, so it always resolved to the
        primary display and the HUD landed on the wrong monitor. The screen
        under the pointer is a far better proxy for where the user is working.
        """
        # A position the user chose wins over anything automatic.
        if self.saved_position is not None:
            spot = self._clamp_onscreen(*self.saved_position)
            if spot is not None:
                self._placing = True
                self.panel.setFrame_display_(
                    NSMakeRect(spot[0], spot[1], PANEL_W, PANEL_H), False)
                self._placing = False
                return

        screen = None
        try:
            point = NSEvent.mouseLocation()
            for candidate in NSScreen.screens():
                f = candidate.frame()
                if (f.origin.x <= point.x <= f.origin.x + f.size.width
                        and f.origin.y <= point.y <= f.origin.y + f.size.height):
                    screen = candidate
                    break
        except Exception:
            screen = None
        if screen is None:
            screen = NSScreen.mainScreen()
        if screen is None:
            return
        vf = screen.visibleFrame()
        x = vf.origin.x + (vf.size.width - PANEL_W) / 2.0
        y = vf.origin.y + self.y_offset
        self._placing = True
        self.panel.setFrame_display_(NSMakeRect(x, y, PANEL_W, PANEL_H), False)
        self._placing = False

    def _appear(self):
        """Make the panel visible immediately, without waiting for a tick."""
        self.view.alpha_target = 1.0
        if self.view.alpha < 0.35:
            self.view.alpha = 0.35   # visible on frame one; the tick eases the rest
        self.panel.setAlphaValue_(self.view.alpha)
        # orderFrontRegardless (not makeKeyAndOrderFront) keeps focus put.
        self.panel.orderFrontRegardless()
        self.view.setNeedsDisplay_(True)

    def showListening_(self, _):
        self.view.state = "listening"
        self.view.amp = 0.0
        self._reposition()
        self._appear()

    def showProcessing_(self, _):
        self.view.state = "processing"
        self._appear()

    def windowDidMove_(self, _note):
        """Remember where the user dropped it, so it reappears there.

        This also fires when *we* move the panel, so programmatic placement is
        flagged — otherwise 'reset position' would immediately re-save the
        auto-placed spot and never actually reset.
        """
        if self._placing:
            return
        frame = self.panel.frame()
        self.saved_position = (float(frame.origin.x), float(frame.origin.y))
        self._save_position()

    @objc.python_method
    def _save_position(self):
        try:
            from . import config as cfg
            conf = cfg.load_config()
            x, y = self.saved_position
            conf.setdefault("overlay", {})["position"] = {"x": x, "y": y}
            with open(cfg.CONFIG_PATH, "w") as f:
                json.dump(conf, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # a HUD that can't save its spot must not break dictation

    def resetPosition_(self, _sender=None):
        """Back to automatic bottom-centre placement."""
        self.saved_position = None
        try:
            from . import config as cfg
            conf = cfg.load_config()
            conf.setdefault("overlay", {})["position"] = None
            with open(cfg.CONFIG_PATH, "w") as f:
                json.dump(conf, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        self._reposition()

    def showIdle_(self, _):
        """Dim, always-visible pill: auto transcribe is armed and listening."""
        self.view.state = "listening"
        self.view.amp = 0.0
        self._reposition()
        self.view.alpha_target = 0.45
        self.view.alpha = max(self.view.alpha, 0.45)
        self.panel.setAlphaValue_(self.view.alpha)
        self.panel.orderFrontRegardless()
        self.view.setNeedsDisplay_(True)

    def hideOverlay_(self, _):
        self.view.alpha_target = 0.0


_controller = None


def start(recorder, y_offset=120.0, position=None, locked=False):
    """Create the panel. Must be called on the main thread before run_forever."""
    global _controller
    app = NSApplication.sharedApplication()
    # Accessory policy = no Dock icon, and activating never steals focus.
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    _controller = OverlayController.alloc().initWithRecorder_(recorder)
    _controller.y_offset = y_offset
    if isinstance(position, dict) and "x" in position and "y" in position:
        _controller.saved_position = (float(position["x"]), float(position["y"]))
    if locked:
        _controller.panel.setIgnoresMouseEvents_(True)
        _controller.panel.setMovableByWindowBackground_(False)
    return _controller


def reset_position():
    if _controller is not None:
        _controller.performSelectorOnMainThread_withObject_waitUntilDone_(
            "resetPosition:", None, False)


def run_forever():
    """Blocks running the Cocoa event loop (main thread)."""
    NSApplication.sharedApplication().run()


def _post(selector):
    if _controller is None:
        return
    _controller.performSelectorOnMainThread_withObject_waitUntilDone_(
        selector, None, False
    )


def show_listening():
    _post("showListening:")


def show_processing():
    _post("showProcessing:")


def show_idle():
    _post("showIdle:")


def hide():
    _post("hideOverlay:")
