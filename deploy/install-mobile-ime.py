"""Install mobile patches before the upstream module, idempotently."""
from pathlib import Path
import shutil

root = Path('/usr/share/selkies/web')
for filename in ('mobile-ime.js', 'mobile-tools.js', 'mobile-tools.css', 'mobile-viewport.js', 'safari-codecs.js'):
    shutil.copyfile(Path('/opt/wechat-hardening')/filename, root/('wechat-'+filename))
    (root/('wechat-'+filename)).chmod(0o644)
index = root/'index.html'
html = index.read_text()
tags = ('<script src="/wechat-safari-codecs.js?v=1"></script>',
        '<script src="/wechat-mobile-viewport.js?v=2"></script>',
        '<script src="/wechat-mobile-ime.js?v=1"></script>',
        '<link rel="stylesheet" href="/wechat-mobile-tools.css?v=1">',
        '<script src="/wechat-mobile-tools.js?v=1"></script>')
if '<head>' not in html:
    raise SystemExit('Unexpected Selkies HTML layout; mobile patches not installed')
# The viewport meta must be parsed before measuring phone dimensions. Classic
# scripts still run before the deferred upstream module at this insertion point.
updated = html
updated = updated.replace('<script src="/wechat-mobile-viewport.js?v=1"></script>', '')
for tag in tags:
    updated = updated.replace(tag, '')
anchor = '<script type="module"'
if anchor not in updated:
    raise SystemExit('Unexpected Selkies module layout; mobile patches not installed')
updated = updated.replace(anchor, ''.join(tags)+anchor, 1)
if updated != html:
    temporary = index.with_name('index.html.new')
    temporary.write_text(updated)
    temporary.chmod(0o644)
    temporary.replace(index)
