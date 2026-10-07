import sys


def main() -> int:
    try:
        from .gui.app import main as gui_main
    except ImportError as e:
        hint = ("Qt could not load a system library. On Debian/Ubuntu: sudo apt install libegl1 libgl1 libxkbcommon0 "
                "libfontconfig1 libdbus-1-3" if any(x in str(e) for x in (".so", "libEGL", "libGL", "xcb")) else
                "Install PySide6 (pip install -r requirements.txt)")
        print(f"GUI unavailable ({e}). {hint}, or use rpgm-hub-cli.", file=sys.stderr)
        return 1
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
