import os

os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import pyarrow  # noqa: F401, E402
import google.protobuf  # noqa: F401, E402
import google.genai  # noqa: F401, E402

import sys  # noqa: E402
from streamlit.web import cli as stcli  # noqa: E402

if __name__ == "__main__":
    sys.argv = ["streamlit", "run", "app.py", "--server.fileWatcherType", "none"]
    stcli.main()
