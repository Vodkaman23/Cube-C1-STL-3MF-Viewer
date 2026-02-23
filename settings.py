"""
Persistent user settings for CubeVi C1 STL Viewer.
Auto-saves on exit, auto-loads on startup.
Stored as JSON next to the executable.
"""

import os
import json

_SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'cubevi_settings.json'
)

# Keys and their defaults — if a key is missing from the file, the default is used.
_DEFAULTS = {
    # View
    'num_views': 16,
    'view_cone_degrees': 40.0,
    'view_blend': 0.7,
    'cubic_blend': 0.0,
    'gamma': 1.0,

    # Material
    'color_preset': 'White',
    'material_preset': 'Matte Plastic',
    'roughness': 0.55,
    'rim_strength': 0.6,
    'ao_strength': 0.4,
    'env_reflect': 0.15,
    'light_intensity': 1.0,

    # Backdrop
    'bg_mode': 0,
    'bg_preset': 'Dark Studio',

    # Lenticular calibration overrides (None = use device config)
    'slope': None,
    'interval': None,
    'x0': None,

    # Camera
    'camera_distance': 1.77,

    # Window
    'window_x': 100,
    'window_y': 100,
    'window_w': 420,
    'window_h': 860,
}


def load_settings(debug=False):
    """Load settings from disk. Returns a dict with all keys guaranteed present."""
    settings = dict(_DEFAULTS)

    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
            # Merge saved values over defaults (ignore unknown keys)
            for k in _DEFAULTS:
                if k in saved:
                    settings[k] = saved[k]
            if debug:
                print(f"Settings loaded from {_SETTINGS_FILE}")
        except Exception as e:
            if debug:
                print(f"Warning: failed to load settings: {e}")
    else:
        if debug:
            print(f"No settings file found, using defaults")

    return settings


def save_settings(settings, debug=False):
    """Save settings dict to disk. Only saves keys that exist in _DEFAULTS."""
    to_save = {}
    for k in _DEFAULTS:
        if k in settings:
            to_save[k] = settings[k]

    try:
        with open(_SETTINGS_FILE, 'w') as f:
            json.dump(to_save, f, indent=2)
        if debug:
            print(f"Settings saved to {_SETTINGS_FILE}")
    except Exception as e:
        if debug:
            print(f"Warning: failed to save settings: {e}")
