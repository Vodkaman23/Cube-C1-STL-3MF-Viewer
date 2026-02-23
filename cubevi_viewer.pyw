"""
CubeVi C1 STL Viewer — Console-free launcher.
Run this file instead of main.py to launch without a terminal window.
All output goes to cubevi_viewer.log in the same directory.
"""

import os
import sys

# Ensure imports resolve relative to this file's directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import main

if __name__ == '__main__':
    main()
