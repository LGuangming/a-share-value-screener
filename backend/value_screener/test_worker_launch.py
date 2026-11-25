"""Shell-free test overlay entrypoint; fail closed before running pipeline."""
import os
import sys
from .test_deploy_guard import main as guard
def main():
    guard()
    os.execv(sys.executable, [sys.executable, "-m", "value_screener.local_pipeline", *sys.argv[1:]])
if __name__ == "__main__":
    main()
