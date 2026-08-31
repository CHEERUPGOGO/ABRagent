# -*- coding: utf-8 -*-
"""PINN workers — 每个 worker 是自包含独立脚本（subprocess 边界）。

公共代码不 import 本包内任何 worker 模块；只通过 registry 的命令行调用。
替换 PINN 包 = 新增/替换 worker 脚本 + registry 一行，互不依赖。
"""
