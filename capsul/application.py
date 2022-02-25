# -*- coding: utf-8 -*-
import dataclasses
import importlib
import json
from pathlib import Path
import types
import inspect

# Nipype import
try:
    from nipype.interfaces.base import Interface as NipypeInterface
# If nipype is not found create a dummy Interface class
except ImportError:
    NipypeInterface = type("Interface", (object, ), {})

from soma.controller import field
from soma.undefined import undefined

from .dataset import Dataset
from .api import Process, Pipeline, Node
from .process.nipype_process import nipype_factory
from .engine.local import LocalEngine



class Capsul:
    '''User entry point to Capsul features. 
    This objects reads Capsul configuration in site and user environments.
    It allows configuration customization and instanciation of a 
    CapsulEngine instance to reach an execution environment.
    
    Example:

        from capsul.api import Capsul
        capsul = Capsul()
        e = capsul.executable('capsul.process.test.test_runprocess.DummyProcess')
        with capsul.engine() as capsul_engine:
            capsul_engine.run(e)

    '''    

    def __init__(self, config_file=None):
        if config_file is None:
            self.config_file = None
            self.config = {}
        else:
            if isinstance(config_file, str):
                self.config_file = Path(config_file)
            else:
                self.config_file = config_file
            with self.config_file.open() as f:
                self.config = json.load(f)
    
    @staticmethod
    def is_executable(item):
        '''Check if the input item is a process class or function with decorator
        '''
        if isinstance(item, type) and item not in (Pipeline, Process) \
                and (issubclass(item, Process) or issubclass(item, NipypeInterface)):
            return True
        if not inspect.isfunction(item):
            return False
        if item.__annotations__:
            return True
        return False

    def executable(self, definition, **kwargs):
        return executable(definition, **kwargs)

    def engine(self):
        engine_config = self.config.get('default', {})
        return LocalEngine(engine_config)

    def dataset(self, path):
        dataset_config_file = path / 'capsul.json'
        with dataset_config_file.open() as f:
            dataset_config = json.load(f)
        path_layout = dataset_config['path_layout']
        return Dataset(path, path_layout)

    def custom_pipeline(self, *args, **kwargs):
        return Pipeline(definition='custom', *args, **kwargs)

def executable(definition, **kwargs):
    '''
    Build a Process instance given a definition string
    '''
        
    result = None
    item = None
    if isinstance(definition, dict):
        result = executable_from_json(None, definition)
    elif isinstance(definition, Node):
        result = type(definition)()
    elif isinstance(definition, type) and issubclass(definition, Node):
        result = definition()
    else:
        if definition.endswith('.json'):
            with open(definition) as f:
                json_executable = json.load(f)
            result =  executable_from_json(definition, json_executable)
        elif definition.endswith('.py') or len(definition.rsplit('.py#')) == 2:
            filename = definition
            object_name = None
            def_item = definition.rsplit('.py#')
            if len(def_item) == 2:
                # class/function name after # in the definition
                object_name = def_item[1]
                filename = def_item[0] + '.py'
            d = {}
            with open(filename) as f:
                exec(f.read(), d, d)
            if object_name:
                process = d.get(object_name)
                if process is None:
                    raise ValueError(
                        f'Invalid executable definition: {definition}')
            else:
                processes = [i for i in d.values()
                             if isinstance(i, type) and issubclass(i, Node)
                                and i not in (Node, Process, Pipeline)]
                if not processes:
                    # TODO: try to find a function process
                    raise ValueError(f'No process class found in {definition}')
                if len(processes) > 1:
                    raise ValueError(
                        f'Several process classes found in {definition}')
                process = processes[0]
            result = executable_from_python(definition, process)
        else:
            elements = definition.rsplit('.', 1)
            if len(elements) > 1:
                module_name, object_name = elements
                module = importlib.import_module(module_name)
                item = getattr(module, object_name, None)
                if item is None:
                    # maybe a sub-module to be imported
                    module = importlib.import_module(definition)
                    if module is not None:
                        item = module
                # check if item is a module containing a single process
                if (inspect.ismodule(item)
                        or (inspect.isclass(item)
                            and not issubclass(item, Node))):
                    items = find_executables(item)
                    if len(items) == 1:
                        module = item
                        item = items[0]
                        definition = '.'.join((definition,
                                               item.__name__.rsplit('.')[-1]))
                    elif len(items) > 1:
                        raise ValueError(
                            f'Several process classes found in {definition}')
                result = executable_from_python(definition, item)

    if result is not None:
        for name, value in kwargs.items():
            setattr(result, name, value)
        return result
    raise ValueError(f'Invalid executable definition: {definition}') 
    
def find_executables(module):
    # report only Node classes, since it's difficult to be sure a function
    # is meant to be a Process or not.
    items = []
    for item in module.__dict__.values():
        if inspect.isclass(item) and issubclass(item, Node) \
                and item not in (Node, Process, Pipeline):
            items.append(item)
    return items

def executable_from_python(definition, item):
    '''
    Build a process instance from a Python object and its definition string.
    '''
    result = None
    # If item is already a Process
    # instance.
    if isinstance(item, Process):
        result = item

    # If item is a Process class.
    elif (isinstance(item, type) and
        issubclass(item, Process)):
        result = item(definition=definition)

    # If item is a Nipye
    # interface instance, wrap this structure in a Process class
    elif isinstance(item, NipypeInterface):
        result = nipype_factory(definition, item)

    # If item is a Nipype Interface class.
    elif (isinstance(item, type) and
        issubclass(item, NipypeInterface)):
        result = nipype_factory(item())

    # If item is a function.
    elif isinstance(item, types.FunctionType):
        annotations = getattr(item, '__annotations__', None)
        if annotations:
            result = process_from_function(item)(definition=definition)
        else:
            raise ValueError(f'Cannot find annotation description to make function {item} a process')

    else:
        raise ValueError(f'Cannot create an executable from {item}')           

    return result

def executable_from_json(definition, json_executable):
    '''
    Build a process instance from a JSON dictionary and its definition string.
    '''
    type = json_executable.get('type')
    if type is None:
        raise ValueError(f'type missing from pipeline defined in {definition}')
    pipeline_definition = json_executable.get('definition')
    if pipeline_definition is None:
        raise ValueError(f'definition missing from pipeline defined in {definition}')
    if type in ('process', 'pipeline'):
        result = executable(pipeline_definition)
        if definition is not None:
            result.definition = definition
        parameters = json_executable.get('parameters')
        if parameters:
            result.import_json(parameters)
    elif type == 'custom_pipeline':
        result = JSONPipeline(definition, pipeline_definition)
        parameters = json_executable.get('parameters')
        if parameters:
            result.import_json(parameters)
    else:
        raise ValueError(f'Invalid executable type in {definition}: {type}')
    return result

def process_from_function(function):
    annotations = {}
    for name, type_ in getattr(function, '__annotations__', {}).items():
        output = name == 'return'
        if isinstance(type_, dataclasses.Field):
            metadata = {}
            metadata.update(type_.metadata)
            metadata.update(type_.metadata.get('_metadata', {}))
            if '_metadata' in metadata:
                del metadata['_metadata']
            metadata['output'] = output
            default=type_.default
            default_factory=type_.default_factory
            kwargs = dict(
                type_=type_.type,
                default=default,
                default_factory=default_factory,
                repr=type_.repr,
                hash=type_.hash,
                init=type_.init,
                compare=type_.compare,
                metadata=metadata)
        else:
            kwargs = dict(
                type_=type_,
                default=undefined,
                output=output)
        if output:
            # "return" cannot be used as a parameter because it is a Python keyword.
            #  Change it to "result"
            name = 'result'
        annotations[name] = field(**kwargs)

    def wrap(self, context):
        kwargs = {i: getattr(self, i) for i in annotations if i != 'result' and getattr(self, i, undefined) is not undefined}
        result = function(**kwargs)
        setattr(self, 'result', result)

    namespace = {
        '__annotations__': annotations,
        'execute': wrap,
    }
    name = f'{function.__name__}_process'
    return type(name, (Process,), namespace)




class JSONPipeline(Pipeline):
    def __init__(self, definition, json_executable):
        if definition is None:
            definition = 'custom_pipeline'
        object.__setattr__(self, 'json_executable' , json_executable)
        super().__init__(definition=definition, autoexport_nodes_parameters=json_executable.get('export_parameters', False))
    
    def pipeline_definition(self):
        exported_parameters = set()
        for name, ejson in self.json_executable['executables'].items():
            e = executable(ejson)
            #e = executable_from_json(f'{self.definition}#{name}', ejson)
            self.add_process(name, e)
        all_links = [(i, False) for i in self.json_executable.get('links', [])]
        all_links += [(i, True) for i in self.json_executable.get('weak_links', [])]
        
        for link_def, weak_link in all_links:
            if isinstance(link_def, (list, tuple)):
                source, dest = link_def
            else:
                source, dest = link_def.split('->')
            if '.' in source:
                if '.' in dest:
                    self.add_link(f'{source}->{dest}',
                                     weak_link=weak_link)
                elif dest in exported_parameters:
                    self.add_link(f'{source}->{dest}',
                                     weak_link=weak_link)
                else:
                    node, plug = source.rsplit('.', 1)
                    self.export_parameter(node, plug, dest,
                                             weak_link=weak_link)
                    exported_parameters.add(dest)
            elif source in exported_parameters:
                self.add_link(f'{source}->{dest}')
            else:
                node, plug = dest.rsplit('.', 1)
                self.export_parameter(node, plug, source,
                                         weak_link=weak_link)
                exported_parameters.add(source)
