from __future__ import annotations

from pathlib import Path

from nshskill import Skill, create_skill_cli

skill = Skill.from_dir(Path(__file__).resolve().parent.parent / "_skill")
main = create_skill_cli("nshconfig", skill)

if __name__ == "__main__":
    main()
