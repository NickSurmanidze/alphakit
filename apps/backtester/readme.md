1. Install uv

curl -Ls https://astral.sh/uv/install.sh | sh

2. create venv

uv venv --python 3.13

3. Install deps

uv sync --extra dev

After changing pyproject.toml, run:

uv sync

To upgrade all pinned versions later:

uv lock --upgrade
uv sync

Activate venv

$ source .venv/bin/activate

Deactivate

```
$ deactivate
```

Check python version

```
python --version
```
