#
# Copyright (c) 2026 MirraNET, Niklas Linz. All rights reserved.
#
# This file is part of the MirraNET project and is licensed under the
# GNU Lesser General Public License v3.0 (LGPLv3).
#
# You may use, distribute and modify this code under the terms
# of the LGPLv3 license. You should have received a copy of the
# license along with this file. If not, see <https://www.gnu.org/licenses/lgpl-3.0.html>
# or contact: niklas.linz@mirranet.de
#
"""FreeXHT: the FreeX TV interface as the whole system of a small box on the TV."""

import os

#: Where the package puts its files (overridable for development and tests).
LIB_DIR = os.environ.get("FREEXHT_LIB", "/usr/lib/freexht")
#: The FreeX TV interface (web/ from FreeX-TV-Apps).
WEB_DIR = os.environ.get("FREEXHT_WEB", "/usr/share/freexht/web")


def version() -> str:
    """The version of the installed package, written by packaging/build-deb.sh."""
    for path in (os.path.join(LIB_DIR, "VERSION"), os.path.join(os.path.dirname(__file__), "..", "..", "VERSION")):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip() or "dev"
        except OSError:
            continue
    return "dev"
