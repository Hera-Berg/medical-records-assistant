"""The downloaded app's entry point: the launcher, and nothing else.

Exactly what ``health-agent app`` runs. The frozen build has no other command;
the full command line is the pip install's, and stays there.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    # A frozen Windows program that starts a child Python process re-enters
    # here; this returns at once in the parent and runs the child's work in it.
    multiprocessing.freeze_support()
    from agent.app.launcher import main

    sys.exit(main(sys.argv[1:]))
