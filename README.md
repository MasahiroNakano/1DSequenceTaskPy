# Pytunnel package
cd X:\src\srcMaNa_20230517_1DSequence\1DSequenceTaskPy
cd Y:\src\srcMaNa_20230517_1DSequence\1DSequenceTaskPy

python src\pytunnel\main.py examples\yaml\protocol_2\protocol_2_level_2_box10.yaml

## Introduction

This repository contains scripts to run virtual corridors using Python 3. It is a sequential navigation task, where mice have to navigate through virtual corridor to reach landmarks in a specific order.
For more details, check [this Notion page](https://polyester-hound-854.notion.site/task-detail-c25802c77d3243f28e957619c238a80c?pvs=4)

2023/05/17
This repository is for use in behavioral box.
Cloned from Shohei Furutachi's project.

# Rules
`sequence`
`run-auto`: reward is given when the mouse has ran random length in the corridor.
`run-lick`: reward is given when the mouse has licked after running more than a random length in the corridor.

For details, check [this Notion page](https://polyester-hound-854.notion.site/task-detail-c25802c77d3243f28e957619c238a80c?pvs=4)

## Installation

To retrieve the code, clone the repository using git:
```
git clone git@github.com:MasahiroNakano/1DSequenceTaskPy.git
```

To install dependencies, I recommend that you first create a virtual
environment (with python or conda) and install dependencies inside using pip.

Using python3 (from a terminal), recommended for Linux users:
```bash
python3.5 -m venv pytunnel_venv
source pytunnel_venv/bin/activate
pip install -r requirements.txt
```

Using conda (from Anaconda Prompt), recommended for Windows users:
```bash
conda create -n pytunnel_venv python=3.5
conda activate pytunnel_venv
pip install -r requirements.txt
```


## Usage

To start a virtual corridor you need to:
- open a terminal
- activate the virtual environment
- run a tunnel script with the corresponding tunnel configuration file.

For example, to run one the example file for the basic tunnel:
```bash
conda activate pytunnel_venv
python src/pytunnel/main.py examples/yaml/protocol_1/protocol_1_level_1.yaml'
```

To know more about the options of a tunnel script, use the `--help`/`-h` option, e.g.
```bash
python src/pytunnel/flip_tunnel.py --help
```
\