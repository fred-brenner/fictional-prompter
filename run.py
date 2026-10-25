#!/usr/bin/env python3
"""Launch the Prompt Playbook workbench."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8420, reload=False)
