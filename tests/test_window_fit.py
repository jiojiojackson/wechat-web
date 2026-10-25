"""Real Xvfb/Openbox regression, only in the disposable networkless runner.

Create fixed-size WeChat-shaped windows; never connect to a production display
or use real accounts. Screen changes use the same RandR path as Selkies.
"""
import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time

from Xlib import X, Xatom, Xutil, display
from selkies.display_utils import resize_display

spec = importlib.util.spec_from_file_location('window_fit', '/opt/wechat-hardening/window-fit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


with tempfile.TemporaryDirectory() as directory:
    env = dict(os.environ, DISPLAY=':93', HOME=directory)
    os.environ['DISPLAY'] = ':93'
    processes = []
    connection = None
    log = open(Path(directory)/'x11.log', 'w')
    try:
        processes.append(subprocess.Popen(['Xvfb', ':93', '-screen', '0', '4096x2160x24',
            '-dpi', '96', '+extension', 'RANDR', '-nolisten', 'tcp', '-ac', '-noreset'],
            env=env, stdout=log, stderr=log))
        for _ in range(60):
            try:
                connection = display.Display(':93')
                break
            except Exception:
                time.sleep(.05)
        assert connection is not None, 'Isolated Xvfb did not start'
        config = Path(directory)/'rc.xml'
        config.write_text(Path('/etc/xdg/openbox/rc.xml').read_text().replace('</applications>',
            '<application class="*"><maximized>yes</maximized></application></applications>'))
        processes.append(subprocess.Popen(['openbox', '--config-file', str(config)],
            env=env, stdout=log, stderr=log))
        root = connection.screen().root
        fitter = module.WindowFit(connection)

        def wait_until(predicate, *, repair=False):
            for _ in range(80):
                if repair:
                    fitter.step()
                connection.sync()
                if predicate():
                    return
                time.sleep(.05)
            raise AssertionError('Window state did not converge')

        wait_until(lambda: bool(fitter.values(root, '_NET_SUPPORTING_WM_CHECK')))
        time.sleep(1)  # Openbox publishes the check window before finishing startup.

        def geometry(window):
            position = root.translate_coords(window, 0, 0)
            dimensions = window.get_geometry()
            return position.x, position.y, dimensions.width, dimensions.height

        def inside(window):
            x, y, width, height = geometry(window)
            screen = root.get_geometry()
            return x >= 0 and y >= 0 and x + width <= screen.width and y + height <= screen.height

        def create(*, fixed=True, wm_class='wechat', kind='NORMAL'):
            window = root.create_window(372, 194, 280, 380, 0, X.CopyFromParent, X.InputOutput,
                                        X.CopyFromParent, background_pixel=0xffffff)
            window.set_wm_name('Disposable login fixture')
            window.set_wm_class(wm_class, wm_class)
            window.set_wm_normal_hints(flags=Xutil.PMinSize | Xutil.PMaxSize,
                min_width=280, min_height=380,
                max_width=280 if fixed else 4096, max_height=380 if fixed else 4096)
            window.change_property(fitter.atom('_MOTIF_WM_HINTS'), fitter.atom('_MOTIF_WM_HINTS'),
                                   32, [2, 0, 0, 0, 0])
            window.change_property(fitter.atom('_NET_WM_WINDOW_TYPE'), Xatom.ATOM, 32,
                                   [fitter.atom('_NET_WM_WINDOW_TYPE_' + kind)])
            window.map()
            connection.sync()
            wait_until(lambda: window.id in fitter.values(root, '_NET_CLIENT_LIST'))
            return window

        login = create()
        wait_until(lambda: all(fitter.atom(name) in fitter.values(login, '_NET_WM_STATE') for name in
            ('_NET_WM_STATE_MAXIMIZED_VERT', '_NET_WM_STATE_MAXIMIZED_HORZ')))
        # Reproduce the actual stale maximized login at x=372 after shrinking.
        assert asyncio.run(resize_display('480x970'))
        wait_until(lambda: root.get_geometry().width == 480)
        login.configure(x=372, y=194)
        connection.sync()
        wait_until(lambda: not inside(login))
        before = geometry(login)
        wait_until(lambda: inside(login) and geometry(login)[:2] == (100, 295), repair=True)
        assert geometry(login)[2:] == (280, 380)
        assert not any(fitter.atom(name) in fitter.values(login, '_NET_WM_STATE') for name in
                       ('_NET_WM_STATE_MAXIMIZED_VERT', '_NET_WM_STATE_MAXIMIZED_HORZ'))
        print(f'PASS: real off-screen login {before} -> centered {geometry(login)} on 480x970', flush=True)

        # Startup directly on a phone and late-created dialogs use current bounds.
        dialog = create(kind='DIALOG')
        wait_until(lambda: geometry(dialog)[:2] == (100, 295) and dialog.id not in fitter.pending, repair=True)
        print('PASS: newly opened phone login/dialog is fully visible', flush=True)

        # Visible user placement must not be overwritten every half second.
        dialog.configure(x=24, y=80)
        connection.sync()
        wait_until(lambda: geometry(dialog)[:2] == (24, 80))
        for _ in range(4):
            fitter.step()
            time.sleep(.05)
        assert geometry(dialog)[:2] == (24, 80), geometry(dialog)
        print('PASS: manual dragging within the screen stays where the user placed it', flush=True)

        for resolution in ('1360x768', '480x970', '1212x560', '480x970'):
            assert asyncio.run(resize_display(resolution))
            wait_until(lambda: inside(login) and inside(dialog), repair=True)
            screen = root.get_geometry()
            target = ((screen.width - 280)//2, (screen.height - 380)//2)
            wait_until(lambda: geometry(login)[:2] == target and geometry(dialog)[:2] == target, repair=True)
        print('PASS: real desktop-to-phone and rotation recenter without changing native window size', flush=True)

        # Unrelated applications and resizable chat windows are outside the scope.
        foreign = create(wm_class='other-app')
        chat = create(fixed=False)
        time.sleep(.2)
        unchanged = [geometry(window) for window in (foreign, chat)]
        for _ in range(4):
            fitter.step()
            time.sleep(.05)
        assert [geometry(window) for window in (foreign, chat)] == unchanged
        print('PASS: other apps and the main resizable chat window remain unchanged', flush=True)

        dialog.unmap()
        connection.sync()
        fitter.step()
        dialog.map()
        connection.sync()
        wait_until(lambda: inside(dialog), repair=True)
        dialog.destroy()
        connection.sync()
        fitter.step()
        print('PASS: hidden, reopened and destroyed dialogs do not break the watcher', flush=True)
    except Exception:
        log.flush()
        print(Path(directory, 'x11.log').read_text()[-2500:], flush=True)
        raise
    finally:
        if connection is not None:
            connection.close()
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        log.close()
