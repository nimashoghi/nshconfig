from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _install_skill(target: Path | None = None, *, global_: bool = False) -> None:
    """Install the nshconfig Claude Code skill to .claude/skills/using-nshconfig/."""
    if global_:
        target = Path.home()
    elif target is None:
        target = Path.cwd()

    skill_dest = target / ".claude" / "skills" / "using-nshconfig"

    # Source skill directory (relative to this file's package)
    skill_source = Path(__file__).resolve().parent.parent / "_skill"
    if not skill_source.exists():
        print(f"Error: skill source not found at {skill_source}", file=sys.stderr)
        sys.exit(1)

    # Clean existing installation
    if skill_dest.exists():
        shutil.rmtree(skill_dest)

    skill_dest.mkdir(parents=True, exist_ok=True)

    # Copy all skill files (SKILL.md and any references/)
    for item in skill_source.iterdir():
        if item.name == "__init__.py" or item.name == "__pycache__":
            continue
        if item.is_file():
            shutil.copy2(item, skill_dest / item.name)
        elif item.is_dir():
            shutil.copytree(item, skill_dest / item.name)

    print(f"Installed nshconfig skill to {skill_dest}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="nshconfig", description="nshconfig CLI tools")
    subparsers = parser.add_subparsers(dest="command")

    # nshconfig skill ...
    skill_parser = subparsers.add_parser("skill", help="Manage Claude Code skills")
    skill_subparsers = skill_parser.add_subparsers(dest="skill_command")

    # nshconfig skill install
    install_parser = skill_subparsers.add_parser(
        "install", help="Install the nshconfig skill for Claude Code"
    )
    install_parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Target directory (default: current working directory)",
    )
    install_parser.add_argument(
        "--global",
        dest="global_",
        action="store_true",
        default=False,
        help="Install at user level (~/.claude/skills/) instead of project level",
    )

    args = parser.parse_args()

    if args.command == "skill" and args.skill_command == "install":
        _install_skill(args.path, global_=args.global_)
    elif args.command == "skill":
        skill_parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
