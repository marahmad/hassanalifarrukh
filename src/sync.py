"""
Backward-compatibility forwarder for src.sync
Redirects to tools.sync_instagram
"""
from tools.sync_instagram import *
from tools.sync_instagram import main

if __name__ == "__main__":
    main()

