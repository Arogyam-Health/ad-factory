#!/usr/bin/env python3
"""Run the existing ChatGPT web automation with terminal-failure detection.

The upstream automation is intentionally left untouched. This wrapper imports it,
replaces only its generated-image wait routine, and then executes the normal CLI.
A visible ChatGPT image-generation failure is treated as a completed failed job
instead of waiting for the full image timeout.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import time
from