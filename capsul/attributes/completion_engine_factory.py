# -*- coding: utf-8 -*-
'''
Instantiate a default :class:`~capsul.attributes.completion_engine.ProcessCompletionEngine`

Classes
=======
:class:`BuiltinProcessCompletionEngineFactory`
----------------------------------------------
'''

from capsul.attributes.completion_engine import ProcessCompletionEngine, \
    PathCompletionEngine, ProcessCompletionEngineFactory
from capsul.attributes.completion_engine_iteration \
    import ProcessCompletionEngineIteration
from capsul.attributes.fom_completion_engine \
    import FomProcessCompletionEngine, FomPathCompletionEngine, \
    FomProcessCompletionEngineIteration
from capsul.pipeline.process_iteration import ProcessIteration


class BuiltinProcessCompletionEngineFactory(ProcessCompletionEngineFactory):
    '''
    '''
    factory_id = 'builtin'

    def get_completion_engine(self, process, name=None):
        '''
        Factory for ProcessCompletionEngine: get an ProcessCompletionEngine
        instance for a node or process in the context of a given process.

        The CapsulEngine should specify which completion system(s) is (are)
        used (FOM, ...)
        If nothing is configured, a ProcessCompletionEngine base instance will
        be returned. It will not be able to perform completion at all, but will
        conform to the API.
        '''
        if hasattr(process, 'completion_engine'):
            return process.completion_engine

        engine = process.get_capsul_engine()

        # FOM
        if 'FomConfig' in engine.study_config.modules and study_config.use_fom:
            try:
                pfom = FomProcessCompletionEngine(process, name)
                if pfom is not None:
                    pfom.create_attributes_with_fom()
                    return pfom
            except KeyError:
                # process not in FOM
                pass

        # iteration
        in_process = process
        if isinstance(in_process, ProcessIteration):
            if isinstance(
                    ProcessCompletionEngine.get_completion_engine(
                        in_process.process),
                    FomProcessCompletionEngine):
                return FomProcessCompletionEngineIteration(process, name)
            else:
                return ProcessCompletionEngineIteration(process, name)

        # standard ProcessCompletionEngine
        return super(BuiltinProcessCompletionEngineFactory,
                     self).get_completion_engine(process, name)
