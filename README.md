# Pytunnel package


## Introduction

This repository contains scripts to run virtual corridors using Python 3. It is a sequential navigation task, where mice have to navigate through virtual corridor to reach landmarks in a specific order.
For more details, check [this Notion page](https://polyester-hound-854.notion.site/Training-protocols-74a76e8f2c1f40a1b6cd5f65401adc66?pvs=4)

There is a separate repository for data analysis : [1DSequenceAnalysis](https://github.com/MasahiroNakano/1DSequenceAnalysis)

2023/05/17
This repository is for use in behavioral box.
Cloned from Shohei Furutachi's project.

## Installation

To retrieve the code, clone the repository using git:
```
git clone git@github.com:MasahiroNakano/1DSequenceTaskPy.git
```

To install dependencies, I recommend that you first create a virtual
environment (with python or conda) and install dependencies inside using pip.

Using conda (from Anaconda Prompt), recommended for Windows users:
```bash
conda create -n py35 python=3.5
conda activate py35
pip install -r requirements.txt
```

Using python3 (from a terminal), recommended for Linux users: (I=masahiro haven't tested this)
```bash
python3.5 -m venv py35
source pytunnel_venv/bin/activate
pip install -r requirements.txt
```

## Usage

To start a virtual corridor you need to:
- open a terminal
- activate the virtual environment
- run a tunnel script with the corresponding tunnel configuration file.

For example, to run one the example file for the basic tunnel:
```bash
conda activate py35
cd /to/this/folder
python src/pytunnel/main.py examples/yaml/desktop/desktop_no_daq.ymal'
```

All the configuration file (`.yaml`) can be found in `examples/yaml`. Each folder corresponds to the training protocols described in [this Notion page](https://polyester-hound-854.notion.site/Training-protocols-74a76e8f2c1f40a1b6cd5f65401adc66?pvs=4)

When testing codes, the most important thing is to specify `nidaq_stub` instead of `nidaq` in `flip_tunnel/io_module`, if your test computer is not connected to the experimental rigs.

```
flip_tunnel:
    io_module: nidaq_stub
```

You can give a manual reward by space bar if you set the following variable True.
```
flip_tunnel:
    manual_reward_with_space: True
```
In the desktop mode, pressing the space bar would mimic the lick. See if your codes as you expect.

To know more about the options of a tunnel script, use the `--help`/`-h` option, e.g.
```bash
python src/pytunnel/flip_tunnel.py --help
```

# Task details that are relevant for the code
For more details, check [this Notion page](https://polyester-hound-854.notion.site/Training-protocols-74a76e8f2c1f40a1b6cd5f65401adc66?pvs=4)

## Rules
`sequence`: Animal needs to visit one goal/landmark and lick to get a reward. Assist-reward may be used.
`run-auto`: reward is given when the mouse has ran random length in the corridor.
`run-lick`: reward is given when the mouse has licked after running more than a random length in the corridor.