"""Muse Code community harness for Omnigent (MSP over ``muse serve``).

This is a PEP 420 sub-package under the ``omnigent.community.harness`` namespace
that core owns. Do NOT add ``__init__.py`` files above this directory: core ships
``omnigent/community/__init__.py`` and ``omnigent/community/harness/__init__.py`` as
``pkgutil.extend_path`` namespace packages, and shipping our own would shadow them.
"""
