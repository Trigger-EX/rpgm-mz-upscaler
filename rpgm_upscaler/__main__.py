import sys


def main() -> int:
    try:
        from .gui.app import main as gui_main
    except ImportError as e:
        print(f"GUI unavailable ({e}). Install PySide6 (pip install -r requirements.txt) "
              "or use rpgm-upscaler-cli.", file=sys.stderr)
        return 1
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
