"""Keep fixed-size WeChat login/dialog windows on the current X11 screen.

WeChat can retain its startup coordinates after RandR shrinks the desktop.
Openbox also marks its fixed-size login window maximized, which prevents a
normal move request. Correct those windows without resizing the desktop,
resetting the stream, changing focus, or constraining the main chat window.
"""
import time

from Xlib import X, display, error, protocol


class WindowFit:
    def __init__(self, connection):
        self.connection = connection
        self.root = connection.screen().root
        self.atoms = {}
        self.screen_size = None
        self.visible = set()
        self.pending = set()

    def atom(self, name):
        if name not in self.atoms:
            self.atoms[name] = self.connection.intern_atom(name)
        return self.atoms[name]

    def values(self, window, name):
        prop = window.get_full_property(self.atom(name), X.AnyPropertyType)
        return list(prop.value) if prop is not None else []

    def send(self, window, name, values):
        event = protocol.event.ClientMessage(
            window=window, client_type=self.atom(name), data=(32, values))
        self.root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)

    def step(self):
        geometry = self.root.get_geometry()
        size = (geometry.width, geometry.height)
        changed = size != self.screen_size
        visible = set()
        for identifier in self.values(self.root, '_NET_CLIENT_LIST'):
            window = self.connection.create_resource_object('window', int(identifier))
            try:
                classes = window.get_wm_class() or ()
                if not any(value.lower() == 'wechat' for value in classes):
                    continue
                attrs = window.get_attributes()
                if attrs.map_state != X.IsViewable or attrs.override_redirect:
                    continue
                types = self.values(window, '_NET_WM_WINDOW_TYPE')
                if types and not any(self.atom(name) in types for name in
                                     ('_NET_WM_WINDOW_TYPE_NORMAL', '_NET_WM_WINDOW_TYPE_DIALOG')):
                    continue
                hints = window.get_wm_normal_hints()
                if (not hints or hints.min_width <= 0 or hints.min_height <= 0 or
                        hints.min_width != hints.max_width or hints.min_height != hints.max_height):
                    continue
                states = self.values(window, '_NET_WM_STATE')
                if self.atom('_NET_WM_STATE_HIDDEN') in states:
                    continue
                visible.add(window.id)
                maximized = [self.atom(name) for name in
                             ('_NET_WM_STATE_MAXIMIZED_VERT', '_NET_WM_STATE_MAXIMIZED_HORZ')]
                if any(value in states for value in maximized):
                    self.send(window, '_NET_WM_STATE', [0, *maximized, 2, 0])
                    # Wait for the WM to restore the actual fixed-size geometry.
                    self.pending.add(window.id)
                    continue
                current = window.get_geometry()
                position = self.root.translate_coords(window, 0, 0)
                frame = self.values(window, '_NET_FRAME_EXTENTS')
                left, right, top, bottom = frame if len(frame) == 4 else (0, 0, 0, 0)
                width, height = current.width + left + right, current.height + top + bottom
                outside = (position.x - left < 0 or position.y - top < 0 or
                           position.x + current.width + right > size[0] or
                           position.y + current.height + bottom > size[1])
                if window.id not in self.visible or changed or outside or window.id in self.pending:
                    # EWMH NorthWestGravity positions the outer window frame.
                    x, y = max(0, (size[0] - width) // 2), max(0, (size[1] - height) // 2)
                    if (position.x - left, position.y - top) != (x, y):
                        flags = X.NorthWestGravity | (1 << 8) | (1 << 9) | (2 << 12)
                        self.send(window, '_NET_MOVERESIZE_WINDOW', [flags, x, y, 0, 0])
                    self.pending.discard(window.id)
            except error.XError:
                # A window may close between the client-list and geometry reads.
                continue
        self.visible = visible
        self.pending.intersection_update(visible)
        self.screen_size = size
        self.connection.flush()


def main():
    while True:
        try:
            connection = display.Display()
            break
        except (error.DisplayConnectionError, OSError):
            time.sleep(1)
    fitter = WindowFit(connection)
    try:
        while True:
            fitter.step()
            time.sleep(0.5)
    finally:
        connection.close()


if __name__ == '__main__':
    main()
