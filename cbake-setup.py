#!/usr/bin/python
import os

CBAKE_LINK = """#!/usr/bin/python

import {subpath}.cbake as cbake

if __name__ == "__main__":
    from sys import argv
    cbake.main(argv)
"""

BAKEFILE = """{{
	"program": "{reponame}",
	"c-flags": [
		"-std=c17",
		"@!DEBUG: -Ofast",
		"@!DEBUG: -DNDEBUG",
		"@DEBUG: -O2",
		"@DEBUG: -g",
		"-Wall",
	],
	"cxx-flags": [
		"-std=c++20",
		"@!DEBUG: -Ofast",
		"@!DEBUG: -DNDEBUG",
		"@DEBUG: -O2",
		"@DEBUG: -g",
		"-Wall",
	],
	"linker-flags": []
}}
"""

def main():
    cbake_path, _ = os.path.split(os.path.abspath(__file__))
    repo_path = os.path.abspath(os.curdir)

    if not cbake_path.startswith(repo_path) or \
       len(cbake_path) <= len(repo_path):
        print("CBake should be nested as a submodule")
        return 1

    diff_path = cbake_path[len(repo_path):].lstrip("/\\")

    subpath = diff_path.replace('/', '.') \
                       .replace('\\', '.')

    _, reponame = os.path.split(repo_path)

    with open("cbake.py", "wt") as f:
        f.write(CBAKE_LINK.format(subpath=subpath))

    if not os.path.exists("bakefile.json"):
        with open("bakefile.json", "wt") as f:
            f.write(BAKEFILE.format(reponame=reponame))


if __name__ == "__main__":
    exit(main())
