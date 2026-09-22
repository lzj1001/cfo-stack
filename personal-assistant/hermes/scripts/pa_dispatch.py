from pathlib import Path
import os
import sys

project_root = Path(os.environ["PA_PROJECT_ROOT"])
sys.path.insert(0, str(project_root / "src"))

from personal_assistant.hermes_entry import dispatch_due, load_env_file

env = dict(os.environ)
home = Path(env.get("HERMES_HOME") or Path.home() / ".hermes")
load_env_file(home / ".env", env)
count = dispatch_due(env)
if count:
    print(f"sent={count}")
