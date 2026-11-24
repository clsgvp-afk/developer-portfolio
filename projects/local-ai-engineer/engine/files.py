import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time


class Files:
    def __init__(self, root, backups, max_bytes=100000):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.backups = Path(backups)
        self.backups.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.path('.')

    def path(self, name):
        p = self.root / name
        if Path(name).is_absolute() or ':' in name or '\x00' in name:
            raise ValueError('Absolute paths, device paths and alternate streams forbidden')
        p = Path(os.path.abspath(p))
        if not p.is_relative_to(self.root):
            raise ValueError('Path escapes workspace')
        # Check root ancestors too: reject junctions, symlinks and all reparse points.
        for part in (p, *p.parents):
            if part.exists() or part.is_symlink():
                info = part.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Reparse points/symlinks forbidden')
        if any(s.lower() in {'.git', '.venv', '.env', 'node_modules', '__pycache__'} or s.lower().startswith('.env.') for s in p.relative_to(self.root).parts):
            raise ValueError('Protected path')
        return p

    def listing(self):
        result = []
        for base, dirs, names in os.walk(self.root, followlinks=False):
            safe = []
            for d in dirs:
                try:
                    self.path(str((Path(base) / d).relative_to(self.root)))
                    safe.append(d)
                except ValueError:
                    pass
            dirs[:] = safe
            for n in names:
                rel = str((Path(base) / n).relative_to(self.root))
                try:
                    p = self.path(rel)
                    if p.stat().st_size <= self.max_bytes:
                        result.append(rel)
                except (ValueError, OSError):
                    pass
                if len(result) >= 400:
                    return result
        return result

    def read(self, name, start=1, end=160):
        p = self.path(name)
        if p.stat().st_size > self.max_bytes:
            raise ValueError('File too large; split it first')
        if not 1 <= start <= end <= start + 300:
            raise ValueError('Invalid line range')
        return '\n'.join(p.read_text(encoding='utf-8-sig').splitlines()[start-1:end])

    def write(self, name, content, old=None, prepared=None):
        p = self.path(name)
        raw = content.encode('utf-8')
        if len(raw) > self.max_bytes:
            raise ValueError('File size limit')
        if p.exists():
            existing = p.read_bytes()
            if old is None:
                raise ValueError('Existing file: use APPLY_PATCH with unique old text')
            if len(existing) > self.max_bytes:
                raise ValueError('File too large')
            bom = existing.startswith(b'\xef\xbb\xbf')
            text = existing.decode('utf-8-sig')
            if '\r\n' in text and '\n' not in text.replace('\r\n', ''):
                # Model excerpts use LF even for files saved by Windows tools.
                old = old.replace('\r\n','\n').replace('\n','\r\n')
                content = content.replace('\r\n','\n').replace('\n','\r\n')
            if not old or text.count(old) != 1:
                raise ValueError('Patch old text must occur exactly once')
            if old == content:
                raise ValueError('No-op patch rejected: old and new text are identical; fix the actual reported failure')
            content = text.replace(old, content, 1)
            raw = content.encode('utf-8-sig' if bom else 'utf-8')
            if len(raw) > self.max_bytes:
                raise ValueError('File size limit')
            (self.backups / (str(time.time_ns()) + '_' + hashlib.sha256(name.encode()).hexdigest()[:12])).write_bytes(existing)
        elif old is not None:
            raise ValueError('Patch target missing')
        p.parent.mkdir(parents=True, exist_ok=True)
        # Stage outside the project tree so an interrupted write cannot appear as source.
        with tempfile.NamedTemporaryFile(dir=self.backups, delete=False) as out:
            staged = Path(out.name)
            try:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
                out.close()
                if prepared:
                    prepared(hashlib.sha256(raw).hexdigest())
                if old is None:
                    os.link(staged, p)
                else:
                    if p.read_bytes() != existing:
                        raise ValueError('Patch target changed while preparing the write')
                    os.replace(staged, p)
            finally:
                out.close()
                staged.unlink(missing_ok=True)
        return 'Saved ' + name

    def fingerprint(self):
        h = hashlib.sha256()
        for base, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {'.git', '.venv', 'node_modules', '__pycache__'})
            for n in sorted(names):
                p = Path(base) / n
                rel = str(p.relative_to(self.root))
                # All visible source files, including files too large for model context.
                self.path(rel)
                h.update(rel.encode())
                with p.open('rb') as f:
                    for block in iter(lambda: f.read(65536), b''):
                        h.update(block)
        return h.hexdigest()
