"""Container health probe without shell or extra dependencies."""

import os
from urllib.request import urlopen


def main() -> None:
    """Exit unsuccessfully unless the configured local endpoint answers."""
    url = os.getenv("HEALTHCHECK_URL", "http://127.0.0.1:8000/health")
    with urlopen(url, timeout=2) as response:
        if response.status != 200:
            raise RuntimeError(f"health check returned HTTP {response.status}")


if __name__ == "__main__":
    main()
