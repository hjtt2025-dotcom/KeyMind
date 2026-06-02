# KeyMind

KeyMind is a privacy-friendly Windows desktop app for visualizing keyboard and mouse activity.

Instead of recording what you type, KeyMind stores local activity statistics such as keystroke counts, key frequency, mouse clicks, scrolling activity and hourly trends. It turns everyday computer usage into a visual and gamified experience.

## Features

- Real-time desktop capsule showing daily keystroke count
- Keyboard and mouse activity statistics
- 24-hour input heatmap
- Daily and weekly activity trends
- Peak activity period analysis
- Local data import and export
- Optional automatic startup on Windows
- Custom capsule themes and dynamic colours
- RPG-style levels and achievement notifications

## Privacy

KeyMind runs locally on your computer.

- It does not upload your data to a server.
- It does not record the text you type.
- It stores statistical information such as key frequencies, click counts and activity trends.
- You can export, import or permanently clear your local data at any time.

## Run From Source

KeyMind currently supports Windows.

1. Install Python 3.10 or later.
2. Install the dependencies:

```bash
pip install -r requirements.txt
```

3. Run the application:

```bash
python src/KeyMind.py
```

## Data Storage

KeyMind generates local files next to the application:

```text
count_record.json
data.js
config.json
```

These files may contain personal activity statistics. They are excluded from Git and should not be uploaded when sharing the project.

## Tech Stack

- Python
- PyQt5 and Qt WebEngine
- pynput
- HTML, CSS and JavaScript

## Releases

Packaged Windows builds can be published on the GitHub Releases page. Avoid committing `.exe` files directly to the repository.

## Status

KeyMind is an experimental personal productivity project. Feedback and suggestions are welcome.
