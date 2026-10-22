"""Remove real portrait files while preserving generated fictional avatars.

The historical static/portraits directory is cleaned if present. In the current
release, assets/portraits/*_avatar.png are generated silhouettes, not real photos,
so they are intentionally retained; only *_real.* files are removed there.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / 'static' / 'portraits']


def main() -> int:
    removed = 0
    for directory in TARGETS:
        if not directory.exists():
            continue
        for path in directory.rglob('*'):
            if path.is_file():
                path.unlink()
                removed += 1
        for path in sorted(directory.rglob('*'), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
    generated_portraits = ROOT / 'assets' / 'portraits'
    if generated_portraits.exists():
        for path in generated_portraits.glob('*_real.*'):
            if path.is_file():
                path.unlink()
                removed += 1
    print(f'Removed {removed} real portrait files. Generated *_avatar.png files were preserved.')
    return removed


if __name__ == '__main__':
    main()
