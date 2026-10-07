import sys


CLI_COMMANDS = {"analyze", "plan", "run", "unpack", "scripts", "detect", "saves", "translate", "translate-game"}


def main() -> int:
    """`rpgm-hub [GAME]` opens the window; `rpgm-hub analyze|plan|run|... ARGS` runs the command line (so a bundled build,
    which has a single executable, offers both)."""
    if len(sys.argv) > 1 and (sys.argv[1] in CLI_COMMANDS or sys.argv[1] == "--cli"):
        from .cli import main as cli_main
        return cli_main(sys.argv[2:] if sys.argv[1] == "--cli" else sys.argv[1:])
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
