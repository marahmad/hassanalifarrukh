#!/usr/bin/env python3
"""
Stand-alone Instagram Media Sync & AI Ingestion Script
Bundled with the workspace skill: instagram-media-sync
"""

import os
import sys
from pathlib import Path

# Set up paths relative to current script
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SKILL_DIR.parent.parent.parent

# Add project root to sys.path
sys.path.insert(0, str(PROJECT_ROOT))

if __name__ == "__main__":
    try:
        from tools.sync_instagram import main
        main()
    except ImportError:
        try:
            from src.sync import main
            main()
        except ImportError:
            print(f"[!] Neither tools.sync_instagram nor src.sync found in {PROJECT_ROOT}.")
            sys.exit(1)
