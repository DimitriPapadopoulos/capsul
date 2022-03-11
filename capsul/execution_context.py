# -*- coding: utf-8 -*-
import os

from .engine.local import LocalEngine

class ExecutionContext:
    def __init__(self, execution_info, tmp):
        self.execution_info = execution_info
        self.tmp = tmp
        self.config = self.execution_info['config']
        for module_name, module_config in self.config['modules'].items():
            module = LocalEngine.module(module_name)
            module.init_execution_context(self)
