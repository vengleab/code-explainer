"""
backend/runtime/ — the layer between the network and the user's code.

sandbox.py    — is this snippet safe to exec, and under which builtins/imports?
serverless.py — the WSGI protocol, GIF/PNG encoding, and the JSON responses.

Deliberately import-free: every endpoint imports the submodules directly, so
nothing here executes during a serverless cold start.
"""
